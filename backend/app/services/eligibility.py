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
from contextvars import ContextVar
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
from .technical import (QUAL_LEVELS, best_qualification, dimensions_on_record, disciplines, equipment_matches, item_matches,
                        keywords_match, nature_keywords, norm_class, norm_unit, parse_dimensions, to_base)

ENGINE_VERSION = "rules-1.1"
RULE_MAP = {
    "fin.avg_turnover": "avg_turnover", "fin.net_worth": "net_worth", "fin.solvency": "solvency",
    "fin.bid_capacity": "bid_capacity", "jv.permitted": "jv_permitted", "jv.max_members": "jv_max_members",
    "jv.lead_share": "jv_lead_share",
}
PASS, FAIL, INSUFF, AMBIG, NA = "pass", "fail", "insufficient_evidence", "ambiguous", "not_applicable"


# ------------------------------------------------------------------ proposal & approval
EXPERIENCE_RULES = ("similar_work", "specific_experience")


def _dims_text(p: dict) -> str:
    dims = p.get("dimensions") or []
    return (" with " + " and ".join(f"{d['name']} ≥ {d['min']} {d.get('unit') or 'm'}" for d in dims)) if dims else ""


def normalise_experience_params(p: dict) -> dict:
    """Reviewer edits arrive as text: 'height 7.5 m; length 10 m' and 'retaining, wall'."""
    out = dict(p)
    if isinstance(out.get("dimensions"), str):
        out["dimensions"] = parse_dimensions(re.sub(r"\b(height|length|depth|width|span|diameter|thickness)\s*(?:≥|>=|:)?\s*",
                                                    r"\1 not less than ", out["dimensions"], flags=re.I))
    if isinstance(out.get("keywords"), str):
        out["keywords"] = [k.strip().lower() for k in out["keywords"].split(",") if k.strip()]
    for k in ("allow_ongoing", "allow_substantial_completion"):
        if isinstance(out.get(k), str):
            out[k] = out[k].strip().lower() in ("yes", "true", "1", "y")
    if (out.get("work_nature") or out.get("work")) and not out.get("keywords"):
        out["keywords"] = nature_keywords(out.get("work_nature") or out.get("work"))
    return out


def _explain(rule_type: str, p: dict) -> str:
    if rule_type == "avg_turnover":
        how = f"best {p.get('best')} of last {p.get('years')}" if p.get("method") == "best_n_of_m" else f"last {p.get('years') or '?'}"
        return f"Average annual turnover over the {how} financial years must be at least ₹{format_inr(p['threshold'])}."
    if rule_type == "similar_work":
        val = f"{p['percent_of_estimate']}% of the estimated cost" if p.get("percent_of_estimate") else f"₹{format_inr(p['amount'])}" if p.get("amount") else "the stated value"
        kind = f" of {p['work_nature']}{_dims_text(p)}" if p.get("work_nature") else ""
        label = "General experience: at least" if p.get("experience_type") == "general" else "At least"
        return (f"{label} {p.get('count')} similar work(s){kind}, each of value not less than {val}, "
                f"{'completed or ongoing' if p.get('allow_ongoing') else 'completed'} within the last "
                f"{p.get('period_years') or '?'} years before the cutoff." +
                (" Substantially completed works are accepted per the clause." if p.get("allow_substantial_completion") else ""))
    if rule_type == "specific_experience":
        val = (f", of value not less than {p['percent_of_estimate']}% of the estimated cost" if p.get("percent_of_estimate")
               else f", of value not less than ₹{format_inr(p['amount'])}" if p.get("amount") else "")
        return (f"Specific experience: {p.get('count') or 1} work(s) of {p.get('work') or 'the stated kind'}{_dims_text(p)}{val}, "
                f"{'completed or ongoing' if p.get('allow_ongoing') else 'completed'} within the last {p.get('period_years') or '?'} years.")
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
    if rule_type == "personnel":
        qual = {"iti": "ITI", "diploma": "Diploma", "graduate": "Graduate (B.E./B.Tech)", "postgraduate": "Post-graduate"}.get(
            p.get("min_qualification") or "", "qualification as per clause")
        disc = "/".join(d.title() for d in p.get("disciplines") or [])
        yrs = p.get("min_experience_years")
        if not p.get("min_qualification") and str(yrs) == "0":
            return f"Key personnel: {p.get('count')} × {p['role']} (no minimum qualification or experience stated in the clause)."
        qual_txt = f"minimum {qual}{' in ' + disc if disc else ''}" if p.get("min_qualification") else (
            f"{disc} background" if disc else "qualification as per clause")
        return (f"Key personnel: {p.get('count')} × {p['role']}, {qual_txt}, with at least "
                f"{yrs if yrs is not None else '?'} years' experience.")
    if rule_type == "equipment":
        cap = f" of at least {p['min_capacity']} {p.get('capacity_unit') or ''}".rstrip() if p.get("min_capacity") else ""
        own = "owned by the bidder" if p.get("ownership") == "owned" else "owned or on lease/hire"
        return f"Plant & machinery: {p.get('count')} × {p['equipment']}{cap}, {own}."
    if rule_type == "physical_quantity":
        basis = {"any_one_year": "in any one financial year", "cumulative": "in total"}.get(p.get("basis") or "", "(one year or total: confirm)")
        dia = f" ({p['min_dia_mm']} mm dia and above)" if p.get("min_dia_mm") else ""
        mat = f" [{p['material']}]" if p.get("material") else ""
        return (f"Executed at least {p['min_quantity']} {p['unit']} of {p['item']}{mat}{dia} {basis} during the last "
                f"{p.get('period_years') or '?'} years.")
    if rule_type == "registration_class":
        acc = ", ".join(p.get("accepted_classes") or [p["class"]])
        return f"Registered{' with ' + p['authority'] if p.get('authority') else ''} in class {acc}" + (" (or a higher class listed by the reviewer)." if p.get("or_above") else ".")
    return rule_type


# ------------------------------------------------------------------ JV sharing suggestions (never applied automatically)
JV_MODES = ("aggregate_sum", "lead_only", "each_member", "any_member", "member_scaled")


def suggest_jv(rule_type: str, split: dict | None) -> dict | None:
    """How JV members combine for this criterion, as read from the tender's JV clause. Shown to the reviewer for
    confirmation and used by the qualification planner; the eligibility engine only uses a confirmed `jv` setting."""
    if rule_type.startswith("jv_"):
        return None
    if rule_type in ("document_present", "registration_class"):
        mode = (split or {}).get("documents_mode") or "each_member"
        return {"mode": mode, "basis": "JV clause" if split and split.get("documents_mode") else "default: every member must hold its own document"}
    if not split:
        return None
    lead, mem = split.get("lead_min_pct"), split.get("member_min_pct")
    applies = (split.get("applies_to") or "all").lower()

    def covers(*words) -> bool:
        return applies in ("all", "") or any(w in applies for w in words)

    minima = {k: v for k, v in (("lead_min_pct", lead), ("member_min_pct", mem)) if v}
    lead_note = (f"; note: the lead partner must individually meet {lead}% of the criteria under clause {split['applies_clause']}"
                 if lead and split.get("applies_clause") else "")
    if rule_type == "specific_experience":
        if split.get("specific_mode"):
            return {"mode": split["specific_mode"], "basis": "JV clause on specific experience" + lead_note}
        return None
    if rule_type == "similar_work":
        if split.get("similar_work_mode"):
            return {"mode": split["similar_work_mode"], "basis": "JV clause"}
        if split.get("general_mode"):
            return {"mode": split["general_mode"], "basis": "JV clause: experience of all partners is aggregated" + lead_note}
        if lead and covers("experience", "similar", "technical"):
            return {"mode": "member_scaled", **minima, "basis": "JV clause: percentage of the similar-work value per member"}
        return None
    if rule_type == "net_worth" and split.get("net_worth_mode"):
        return {"mode": split["net_worth_mode"], "basis": "JV clause"}
    if not split.get("combined"):
        return None
    words = {"avg_turnover": ("financial", "turnover"), "net_worth": ("financial", "net worth"), "bid_capacity": ("financial", "bid capacity"),
             "physical_quantity": ("physical", "quantit", "technical"), "personnel": ("personnel", "manpower", "staff"),
             "equipment": ("equipment", "machinery", "plant")}.get(rule_type, ())
    use_minima = minima and (covers(*words) if rule_type not in ("personnel", "equipment") else any(w in applies for w in words))
    return {"mode": "aggregate_sum", **(minima if use_minima else {}), "basis": "JV clause: members' figures are added"}


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
        p = {k: v for k, v in value.items() if k != "jv_hint"}
        p["group"] = "similar_work"
        p["similarity_key"] = "exp.general"  # the alternatives (1 × 80% / 2 × 50% / ...) share one similarity decision
        p.setdefault("substantial_threshold_pct", None)
        return "similar_work", p
    if key.startswith("exp.specific."):
        p = {k: v for k, v in value.items() if k != "jv_hint"}
        p["similarity_key"] = key
        p.setdefault("count", 1)
        return "specific_experience", p
    if key.startswith("tech.personnel."):
        return "personnel", {k: value.get(k) for k in ("role", "count", "min_qualification", "disciplines", "min_experience_years")}
    if key.startswith("tech.equipment."):
        return "equipment", {k: value.get(k) for k in ("equipment", "count", "min_capacity", "capacity_unit", "ownership")}
    if key.startswith("tech.quantity."):
        return "physical_quantity", {k: value.get(k) for k in ("item", "min_quantity", "unit", "basis", "period_years",
                                                                "min_dia_mm", "material")}
    if key == "tech.registration_class":
        return "registration_class", {k: value.get(k) for k in ("class", "accepted_classes", "authority", "or_above")}
    if key == "doc.contractor_registration" and value.get("class"):
        return "registration_class", {"class": value["class"], "accepted_classes": [value["class"]],
                                      "authority": value.get("authority")}
    if key.startswith("doc."):
        return "document_present", {"evidence_category": value.get("evidence_category", "other")}
    return None


def propose_rules(db: Session, tender: Tender) -> int:
    n = 0
    reqs = list(db.scalars(select(Requirement).where(Requirement.tender_id == tender.id)))
    split = _requirement_value(db, tender, "jv.criteria_split")
    similar_def = (_requirement_value(db, tender, "tech.similar_definition") or {}).get("definition")
    has_tech_registration = any(r.key == "tech.registration_class" for r in reqs)
    for req in reqs:
        v = current_version(req)
        if v is None or v.review_status == "rejected":
            continue
        if req.key == "doc.contractor_registration" and has_tech_registration:
            continue  # the class-specific rule covers it
        mapped = _params_for(req.key, v.value)
        if not mapped:
            continue
        rt, params = mapped
        hint = v.value.get("jv_hint") if isinstance(v.value, dict) else None
        sugg = ({"mode": hint["mode"], "basis": f"JV note under this criterion: “{hint.get('text', '')[:160]}”"}
                if hint and hint.get("mode") else suggest_jv(rt, split))
        if sugg:
            params["jv_suggested"] = sugg
        if rt == "similar_work" and similar_def:
            params["similar_definition"] = similar_def
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
        if rule.rule_type in EXPERIENCE_RULES:
            merged = normalise_experience_params(merged)
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


# Per-call memo used by the qualification planner, which evaluates the same evidence for many JV combinations.
_CACHE: ContextVar[dict | None] = ContextVar("eligibility_cache", default=None)


def evidence_for(db: Session, tender: Tender, entity: LegalEntity, categories: list[str]) -> EvidenceView:
    cache = _CACHE.get()
    key = ("ev", tender.id, entity.id, tuple(sorted(categories)))
    if cache is not None and key in cache:
        return cache[key]
    out = _evidence_for(db, tender, entity, categories)
    if cache is not None:
        cache[key] = out
    return out


def _evidence_for(db: Session, tender: Tender, entity: LegalEntity, categories: list[str]) -> EvidenceView:
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
    cache = _CACHE.get()
    ck = ("req", tender.id, key)
    if cache is not None and ck in cache:
        return cache[ck]
    req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == key))
    v = current_version(req) if req else None
    val = v.value if v and v.review_status != "rejected" else None
    if cache is not None:
        cache[ck] = val
    return val


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


def similarity_decision(db, tender_id: int, evidence_id: int, key: str | None) -> EvidenceLink | None:
    """A reviewer's per-tender decision that a work is (accepted) or is not (rejected) similar for this criterion."""
    if not key:
        return None
    cache = _CACHE.get()
    ck = ("sim", tender_id, evidence_id, key)
    if cache is not None and ck in cache:
        return cache[ck]
    link = db.scalar(select(EvidenceLink).where(EvidenceLink.tender_id == tender_id, EvidenceLink.evidence_id == evidence_id,
                                                EvidenceLink.purpose == "similarity", EvidenceLink.criterion_key == key)
                     .order_by(EvidenceLink.id.desc()).limit(1))
    if cache is not None:
        cache[ck] = link
    return link


def _similarity(db, tender, r: EvidenceRecord, p: dict) -> tuple[str, str]:
    """(state, reason): confirmed / rejected / pending (looks similar, a person must confirm) / no_match."""
    link = similarity_decision(db, tender.id, r.id, p.get("similarity_key"))
    if link is not None:
        return ("confirmed", "confirmed similar for this tender") if link.status == "accepted" else (
            "rejected", "marked not similar for this tender")
    f = r.fields
    kws = p.get("keywords") or []
    if not kws:  # older rules carry no description of the work: the vault's similarity flag decides
        return ("confirmed", "marked similar") if f.get("is_similar") is True else ("pending", "similarity not confirmed by reviewer")
    desc = " ".join(str(f.get(k) or "") for k in ("work_name", "scope_of_work", "dimensions")) + " " + (r.title or "")
    ok, missing = keywords_match(kws, desc)
    nature = p.get("work_nature") or p.get("work") or "the required kind of work"
    if ok and f.get("is_similar") is True:
        return "confirmed", f"marked similar and the description matches '{nature}'"
    if ok:
        return "pending", f"description matches '{nature}': confirm it is similar for this tender"
    return "no_match", f"description does not mention {', '.join(missing)}: mark it similar if it is"


def _experience(db, tender, entities: list[LegalEntity], p: dict, cutoff, label: str) -> Outcome:
    """General experience (similar works of a value) and specific experience (works of a kind and size).

    A work counts only if it is within the qualifying period, completed (or ongoing / substantially completed where
    the clause allows it), of the required value, of the required size, and similar per a person's decision."""
    count = int(p.get("count") or 0) or 1
    estimate = try_D((_requirement_value(db, tender, "commercial.estimate") or {}).get("amount"))
    thr, basis = None, "no value limit in the clause"
    if p.get("amount"):
        thr, basis = try_D(p["amount"]), "fixed amount in clause"
    elif p.get("percent_of_estimate"):
        if estimate is None:
            return Outcome(AMBIG, "Threshold depends on the estimated cost, which is not available.",
                           missing=["Estimated cost (commercial.estimate)"])
        thr = q(estimate * D(p["percent_of_estimate"]) / Decimal(100), 2)
        basis = f"{p['percent_of_estimate']}% × estimate ₹{format_inr(estimate)}"
    elif label == "similar_work":
        return Outcome(AMBIG, "Value threshold not defined in the rule.", missing=["similar work threshold"])
    period = p.get("period_years")
    if not period:
        return Outcome(AMBIG, "Qualifying period not confirmed.", missing=["period_years"])
    start = date(cutoff.year - int(period), cutoff.month, min(cutoff.day, 28))
    esc_rate = try_D(p.get("escalation_pct_per_year")) if p.get("escalation_authorized") else None
    req_dims = [(d["name"], try_D(d.get("min"))) for d in (p.get("dimensions") or []) if try_D(d.get("min")) is not None]
    rows, qualifying, incomplete, to_confirm, excluded = [], [], [], [], []
    for ent in entities:
        ev = evidence_for(db, tender, ent, ["completion_certificate", "performance_certificate"])
        excluded = excluded + ev.excluded
        for r in ev.usable:
            f = r.fields
            val = try_D(f.get("executed_value") or f.get("value"))
            status = str(f.get("status") or "completed").strip().lower().replace(" ", "_").replace("-", "")
            ongoing = status in ("ongoing", "in_progress", "work_in_progress")
            cdate = f.get("completion_date") or (f.get("as_on") or (r.issue_date.isoformat() if r.issue_date else None) if ongoing else None)
            row = {"evidence_id": r.id, "entity": ent.legal_name, "work": f.get("work_name") or r.title,
                   "value": str(val) if val is not None else None, "completion_date": cdate, "status": status}
            rows.append(row)
            sim, why = _similarity(db, tender, r, p)
            row["similarity"], row["similarity_note"] = sim, why
            if sim == "rejected":
                row["result"] = why
                continue
            if (thr is not None and val is None) or not cdate:
                row["result"] = "executed value or completion date not recorded" if thr is not None else "completion date not recorded"
                incomplete.append(row["work"])
                continue
            if ongoing and not p.get("allow_ongoing"):
                row["result"] = "ongoing work; the clause asks for completed works"
                continue
            try:
                cd = date.fromisoformat(str(cdate)[:10])
            except ValueError:
                row["result"] = f"date '{cdate}' not understood (use YYYY-MM-DD)"
                incomplete.append(row["work"])
                continue
            if not (start <= cd <= cutoff):
                row["result"] = f"outside qualifying period {start} to {cutoff}"
                continue
            if status == "substantially_completed":
                if not p.get("allow_substantial_completion"):
                    row["result"] = "substantially completed works not allowed by clause"
                    continue
                st, pct = try_D(p.get("substantial_threshold_pct")), try_D(f.get("completed_pct"))
                if st is None or pct is None:
                    row["result"] = "substantial-completion threshold or evidence missing"
                    incomplete.append(row["work"])
                    continue
                if pct < st:
                    row["result"] = f"completed {pct}% < required {st}%"
                    continue
            if req_dims:
                have = dimensions_on_record(" ".join(str(f.get(k) or "") for k in ("dimensions", "scope_of_work", "work_name")))
                row["dimensions"] = {k: str(v) for k, v in have.items()}
                gaps = [n for n, _ in req_dims if n not in have]
                short = [f"{n} {have[n]} m < {m} m" for n, m in req_dims if n in have and have[n] < m]
                if short:
                    row["result"] = "smaller than required: " + "; ".join(short)
                    continue
                if gaps:
                    row["result"] = f"{', '.join(gaps)} not recorded (enter the sizes from the certificate)"
                    incomplete.append(row["work"])
                    continue
            if thr is not None:
                eff = val
                if esc_rate is not None:
                    yrs = Decimal((cutoff - cd).days) / Decimal("365")
                    eff = q(val * (1 + esc_rate / 100) ** int(yrs), 2)
                    row["escalated_value"] = str(eff)
                if eff < thr:
                    row["result"] = f"value below ₹{format_inr(thr)}"
                    continue
            if sim == "no_match":
                row["result"] = f"meets value/size/period, but {why}"
                to_confirm.append(row["work"])
                continue
            if sim == "pending":
                row["result"] = f"meets value/size/period; {why}"
                incomplete.append(row["work"])
                to_confirm.append(row["work"])
                continue
            row["result"] = "qualifies"
            qualifying.append(r.id)
    calc = {"threshold": str(thr) if thr is not None else None, "threshold_basis": basis,
            "period": [start.isoformat(), cutoff.isoformat()], "works": rows, "required_count": count,
            "qualifying_count": len(qualifying), "measure": str(len(qualifying)), "excluded_evidence": excluded,
            "work_nature": p.get("work_nature") or p.get("work"), "dimensions": p.get("dimensions") or [],
            "similarity_key": p.get("similarity_key")}
    what = "similar work(s)" if label == "similar_work" else "work(s) of the specified kind"
    if len(qualifying) >= count:
        return Outcome(PASS, f"{len(qualifying)} qualifying {what} ≥ {count} required.", calc, evidence_ids=qualifying)
    if incomplete:
        return Outcome(INSUFF, f"Only {len(qualifying)} qualifying; {len(incomplete)} work record(s) incomplete or awaiting "
                               f"similarity confirmation.", calc,
                       [f"Confirm similarity: {t}" if t in to_confirm else f"Complete: {t}" for t in dict.fromkeys(incomplete)],
                       "Record value, completion date and sizes from each certificate; confirm similarity for this tender "
                       "in the eligibility matrix.", qualifying)
    if not rows:
        return Outcome(INSUFF, "No verified completion certificates on record.", calc,
                       [f"Completion certificates for {p.get('work_nature') or p.get('work') or 'similar works'}"],
                       "Upload completion certificates.")
    remedy = ("Mark a work similar if its description differs from the tender's wording, or bid with a JV partner who has "
              "this experience (if the JV clause lets members' experience count).")
    return Outcome(FAIL, f"Only {len(qualifying)} qualifying {what}; {count} required.", calc,
                   [f"Confirm similarity: {t}" for t in to_confirm], remedy, qualifying)


def eval_similar_work(db, tender, entity, p, cutoff) -> Outcome:
    return _experience(db, tender, [entity], p, cutoff, "similar_work")


def eval_specific_experience(db, tender, entity, p, cutoff) -> Outcome:
    return _experience(db, tender, [entity], p, cutoff, "specific_experience")


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


# ------------------------------------------------------------------ technical criteria
# These take a list of entities so a JV's resources can be pooled when the tender allows it.
_GENERIC_ROLE_WORDS = {"engineer", "manager", "officer", "site", "project", "in", "charge", "incharge", "senior", "junior",
                       "assistant", "key", "staff", "chief", "deputy", "resident", "the", "of", "and", "for", "cum", "graduate",
                       "qualified", "experienced", "technical", "supervisor", "head", "lead", "team", "leader", "member"}
_DISCIPLINE_WORDS = {"civil", "structural", "mechanical", "mech", "electrical", "elec", "electronics", "environmental",
                     "chemical", "computer", "it"}


def _role_fits(role: str, f: dict, title: str | None) -> tuple[bool | None, list[str]]:
    """A post with a speciality in its name ('Safety Officer', 'Geologist') needs a person whose designation or
    qualification shows it. Generic posts ('Site Engineer', 'Project Manager') are judged on qualification only.
    Returns (True / False / None when the designation is not recorded, the speciality words)."""
    terms = [w for w in re.findall(r"[a-z]+", (role or "").lower()) if w not in _GENERIC_ROLE_WORDS and w not in _DISCIPLINE_WORDS]
    if not terms:
        return True, []
    text = " ".join(str(f.get(k) or "") for k in ("current_designation", "qualification", "discipline", "role")) + " " + (title or "")
    words = re.findall(r"[a-z]+", text.lower())
    if any(w.startswith(t[:max(4, len(t) - 2)]) for t in terms for w in words):
        return True, terms
    return (None if not f.get("current_designation") else False), terms


def _personnel(db, tender, entities: list[LegalEntity], p, cutoff, used: set | None = None) -> Outcome:
    need = int(p.get("count") or 0)
    lvl = p.get("min_qualification")
    exp = try_D(p.get("min_experience_years"))
    role = p.get("role") or "position"
    if not need or (lvl is None and exp is None):
        return Outcome(AMBIG, f"{role}: number of persons, minimum qualification or experience not confirmed.",
                       missing=[m for m, ok in (("number of persons", need), ("minimum qualification", lvl), ("years of experience", exp is not None)) if not ok])
    discs = set(p.get("disciplines") or [])
    rows, qualifying, incomplete, excluded = [], [], [], []
    for ent in entities:
        ev = evidence_for(db, tender, ent, ["personnel"])
        excluded = excluded + ev.excluded
        for r in ev.usable:
            f = r.fields
            qtext = " ".join(str(f.get(k) or "") for k in ("qualification", "discipline"))
            have, have_disc, years = best_qualification(qtext), set(disciplines(qtext)), try_D(f.get("experience_years"))
            row = {"evidence_id": r.id, "entity": ent.legal_name, "person": f.get("person_name") or r.title,
                   "qualification": f.get("qualification"), "experience_years": f.get("experience_years"),
                   "designation": f.get("current_designation")}
            rows.append(row)
            role_ok, role_terms = _role_fits(role, f, r.title)
            if used is not None and r.id in used:
                row["result"] = "already counted for another position"
            elif not _valid_at(r, cutoff):
                row["result"] = f"record expires {r.expiry_date}, before the cutoff"
            elif (lvl and have is None) or (exp and years is None):
                row["result"] = "qualification or years of experience not recorded"
                incomplete.append(row["person"])
            elif role_ok is None:
                row["result"] = f"designation not recorded; the post needs {'/'.join(role_terms)} experience"
                incomplete.append(row["person"])
            elif role_ok is False:
                row["result"] = f"designation/qualification does not show {'/'.join(role_terms)}"
            elif lvl and QUAL_LEVELS.index(have) < QUAL_LEVELS.index(lvl):
                row["result"] = f"{have} is below the required {lvl}"
            elif discs and not (have_disc & discs):
                row["result"] = f"discipline {', '.join(sorted(have_disc)) or 'not stated'}; needs {', '.join(sorted(discs))}"
            elif exp is not None and years is not None and years < exp:
                row["result"] = f"{years} years < {exp} required"
            else:
                row["result"] = "qualifies (spare)"
                qualifying.append((years, r.id, row))
    qualifying.sort(key=lambda x: x[0])  # assign the least senior person who still qualifies; seniors stay free for stricter posts
    chosen = qualifying[:need]
    for _, rid, row in chosen:
        row["result"] = "assigned"
    if used is not None:
        used.update(rid for _, rid, _ in chosen)
    calc = {"role": role, "required_count": need, "qualifying_count": len(qualifying), "measure": str(len(qualifying)),
            "threshold": str(need), "people": rows, "excluded_evidence": excluded}
    names = ", ".join(r["person"] for _, _, r in chosen)
    if len(chosen) >= need:
        return Outcome(PASS, f"{role}: {need} of {need} filled ({names}).", calc, evidence_ids=[rid for _, rid, _ in chosen])
    short = need - len(chosen)
    if incomplete:
        return Outcome(INSUFF, f"{role}: {len(chosen)} of {need} filled; {len(incomplete)} CV record(s) incomplete.", calc,
                       [f"Qualification and experience for {n}" for n in incomplete],
                       "Record each person's qualification and total years of experience from the CV and certificates.",
                       [rid for _, rid, _ in chosen])
    if not rows:
        return Outcome(INSUFF, f"{role}: no verified CVs / personnel records on file.", calc,
                       [f"CV with qualification and experience for {need} × {role}"],
                       "Add CVs, degree certificates and experience proof of the staff you will deploy.")
    return Outcome(FAIL, f"{role}: only {len(chosen)} of {need} filled; {short} more qualified person(s) needed.", calc,
                   [f"{short} × {role} meeting the qualification and experience"],
                   "Recruit or engage qualified staff (with a commitment letter), or bid with a JV partner who has them.",
                   [rid for _, rid, _ in chosen])


def _equipment(db, tender, entities: list[LegalEntity], p, cutoff, used: set | None = None) -> Outcome:
    need = D(p.get("count") or 0)
    name = p.get("equipment")
    if not need or not name:
        return Outcome(AMBIG, "Equipment type or number required not confirmed.", missing=["equipment type / number"])
    min_cap = try_D(p.get("min_capacity"))
    req_unit, req_factor = norm_unit(p.get("capacity_unit"))
    owned_only = p.get("ownership") == "owned"
    rows, picked, incomplete, excluded = [], [], [], []
    total = Decimal(0)
    for ent in entities:
        ev = evidence_for(db, tender, ent, ["machinery"])
        excluded = excluded + ev.excluded
        for r in ev.usable:
            f = r.fields
            etype = f.get("equipment_type") or r.title
            if not equipment_matches(name, etype):
                continue
            qty = try_D(f.get("quantity")) or Decimal(1)
            row = {"evidence_id": r.id, "entity": ent.legal_name, "equipment": etype, "quantity": str(qty),
                   "capacity": f"{f.get('capacity') or ''} {f.get('capacity_unit') or ''}".strip() or None,
                   "ownership": f.get("ownership") or "owned"}
            rows.append(row)
            own = str(f.get("ownership") or "owned").lower()
            if used is not None and r.id in used:
                row["result"] = "already counted for another item"
                continue
            if not _valid_at(r, cutoff):
                row["result"] = f"record (lease/registration) expires {r.expiry_date}, before the cutoff"
                continue
            if owned_only and own.startswith(("lease", "hire", "rent")):
                row["result"] = "leased/hired; the tender requires owned equipment"
                continue
            if min_cap is not None:
                cap = try_D(f.get("capacity"))
                unit, factor = norm_unit(f.get("capacity_unit") or p.get("capacity_unit"))
                if cap is None:
                    row["result"] = "capacity not recorded"
                    incomplete.append(etype)
                    continue
                if req_unit and unit != req_unit:
                    row["result"] = f"capacity unit {unit} cannot be compared with {req_unit}"
                    continue
                if cap * factor < min_cap * req_factor:
                    row["result"] = f"capacity {cap} below {min_cap}"
                    continue
            if total >= need:
                row["result"] = "qualifies (spare)"
                continue
            row["result"] = "counted"
            total += qty
            picked.append(r.id)
    if used is not None:
        used.update(picked)
    calc = {"equipment": name, "required_count": str(need), "available": str(total), "measure": str(total),
            "threshold": str(need), "items": rows, "excluded_evidence": excluded}
    if total >= need:
        return Outcome(PASS, f"{name}: {total} available vs {need} required.", calc, evidence_ids=picked)
    if incomplete:
        return Outcome(INSUFF, f"{name}: {total} of {need} confirmed; capacity missing on {len(incomplete)} record(s).", calc,
                       [f"Capacity of {x}" for x in incomplete], "Record the rated capacity from the RC book / invoice.", picked)
    if not rows:
        return Outcome(INSUFF, f"{name}: no verified ownership or lease records on file.", calc,
                       [f"Ownership (invoice/RC) or lease agreement for {need} × {name}"],
                       "Add the purchase invoice / registration or a lease or hire agreement valid at the cutoff.")
    return Outcome(FAIL, f"{name}: only {total} of {need} available.", calc, [f"{need - total} more × {name}"],
                   "Hire or lease the shortfall (if the tender allows leased equipment), or bid with a JV partner.", picked)


def _quantity(db, tender, entities: list[LegalEntity], p, cutoff, used: set | None = None) -> Outcome:
    item, min_q, unit = p.get("item"), try_D(p.get("min_quantity")), p.get("unit")
    missing = [m for m, ok in (("item", item), ("minimum quantity", min_q is not None), ("unit", unit),
                               ("one year or cumulative", p.get("basis")), ("period (years)", p.get("period_years"))) if not ok]
    if missing:
        return Outcome(AMBIG, "Physical-quantity rule not fully confirmed.", missing=missing)
    req, base = to_base(min_q, unit)
    fys = financial_years_before(cutoff, int(p["period_years"]))
    min_dia = try_D(p.get("min_dia_mm"))
    material = p.get("material")
    per_fy: dict[str, Decimal] = {}
    rows, used_ids, incomplete, excluded = [], [], [], []
    for ent in entities:
        ev = evidence_for(db, tender, ent, ["executed_quantity"])
        excluded = excluded + ev.excluded
        for r in ev.usable:
            f = r.fields
            offered = f"{f.get('item') or r.title} {f.get('material') or ''}"
            if not item_matches(item, offered, material):
                continue
            qty, b = to_base(f.get("quantity"), f.get("unit"))
            fy = _norm_fy(f.get("fy") or "")
            row = {"evidence_id": r.id, "entity": ent.legal_name, "work": f.get("work_name") or r.title, "item": f.get("item"),
                   "quantity": f.get("quantity"), "unit": f.get("unit"), "fy": f.get("fy")}
            rows.append(row)
            dia = try_D(f.get("dia_mm"))
            if qty is None or not f.get("fy"):
                row["result"] = "quantity or financial year not recorded"
                incomplete.append(row["work"])
            elif b != base:
                row["result"] = f"unit {f.get('unit')} cannot be compared with {unit}"
            elif fy not in fys:
                row["result"] = f"FY {fy} outside the last {p['period_years']} years"
            elif min_dia is not None and dia is None:
                row["result"] = "pipe diameter not recorded"
                incomplete.append(row["work"])
            elif min_dia is not None and dia < min_dia:
                row["result"] = f"diameter {dia} mm below {min_dia} mm"
            else:
                row["result"] = "counted"
                per_fy[fy] = per_fy.get(fy, Decimal(0)) + qty
                used_ids.append(r.id)
    if p["basis"] == "any_one_year":
        best = max(per_fy.items(), key=lambda x: x[1], default=(None, Decimal(0)))
        achieved, how = best[1], f"best year FY {best[0]}" if best[0] else "no qualifying year"
    else:
        achieved, how = sum(per_fy.values(), Decimal(0)), f"total over FY {fys[-1]} to {fys[0]}"
    calc = {"item": item, "required": str(req), "unit": base, "achieved": str(achieved), "measure": str(achieved),
            "threshold": str(req), "basis": p["basis"], "by_fy": {k: str(v) for k, v in per_fy.items()}, "how": how,
            "records": rows, "excluded_evidence": excluded}
    msg = f"{item}: {achieved:,} {base} ({how}) vs {req:,} {base} required."
    if achieved >= req:
        return Outcome(PASS, msg, calc, evidence_ids=used_ids)
    if incomplete:
        return Outcome(INSUFF, msg + f" {len(incomplete)} record(s) incomplete.", calc,
                       [f"Quantity/FY/diameter for {w}" for w in incomplete],
                       "Enter quantities executed per financial year from the completion certificates.", used_ids)
    if not rows:
        return Outcome(INSUFF, f"{item}: no executed-quantity records on file.", calc,
                       [f"Certificate showing {item} executed per financial year"],
                       "Add the quantities executed (from completion certificates / measurement books) per financial year.")
    return Outcome(FAIL, msg, calc, [f"{(req - achieved):,} {base} more of {item}"],
                   "Consider a JV partner who has executed this quantity, if the tender lets JV members add quantities.", used_ids)


def eval_registration_class(db, tender, entity, p, cutoff) -> Outcome:
    accepted = [c for c in (p.get("accepted_classes") or ([p["class"]] if p.get("class") else [])) if c]
    if not accepted:
        return Outcome(AMBIG, "Accepted registration class(es) not confirmed.", missing=["accepted classes"])
    acc = {norm_class(c) for c in accepted}
    auth = re.sub(r"[^a-z]", "", (p.get("authority") or "").lower())
    ev = evidence_for(db, tender, entity, ["registration"])
    seen = []
    for r in ev.usable:
        cls = r.fields.get("class")
        if not cls:
            continue
        ra = re.sub(r"[^a-z]", "", str(r.fields.get("authority") or "").lower())
        if auth and ra and auth not in ra and ra not in auth:
            seen.append(f"{cls} with {r.fields.get('authority')} (different authority)")
            continue
        if not _valid_at(r, cutoff):
            seen.append(f"{cls} (expires {r.expiry_date}, before the cutoff)")
            continue
        if norm_class(cls) in acc:
            return Outcome(PASS, f"Registered in class {cls}{' with ' + str(r.fields.get('authority')) if r.fields.get('authority') else ''}, valid at {cutoff}.",
                           {"class": cls, "accepted": accepted}, evidence_ids=[r.id])
        seen.append(f"class {cls}")
    if seen:
        note = " If a higher class is acceptable ('or above'), add it to the accepted classes." if p.get("or_above") else ""
        return Outcome(FAIL, f"Registration on record ({'; '.join(seen)}) is not in the accepted class(es) {', '.join(accepted)}.{note}",
                       {"accepted": accepted, "on_record": seen}, remedy="Apply for upgradation, or bid with a JV partner registered in the class (if the tender allows).")
    return Outcome(INSUFF, f"No verified registration certificate with its class recorded for {entity.legal_name}.",
                   {"accepted": accepted, "excluded_evidence": ev.excluded}, [f"Registration certificate ({', '.join(accepted)})"],
                   "Upload the registration certificate and record its class and authority.")


def eval_personnel(db, tender, entity, p, cutoff, used: set | None = None) -> Outcome:
    return _personnel(db, tender, [entity], p, cutoff, used)


def eval_equipment(db, tender, entity, p, cutoff, used: set | None = None) -> Outcome:
    return _equipment(db, tender, [entity], p, cutoff, used)


def eval_quantity(db, tender, entity, p, cutoff, used: set | None = None) -> Outcome:
    return _quantity(db, tender, [entity], p, cutoff, used)


SINGLE = {"avg_turnover": eval_avg_turnover, "similar_work": eval_similar_work, "net_worth": eval_net_worth,
          "solvency": eval_solvency, "document_present": eval_document, "bid_capacity": eval_bid_capacity,
          "personnel": eval_personnel, "equipment": eval_equipment, "physical_quantity": eval_quantity,
          "registration_class": eval_registration_class, "specific_experience": eval_specific_experience}
MULTI = {"personnel": _personnel, "equipment": _equipment, "physical_quantity": _quantity}
USES_POOL = set(MULTI)  # criteria whose evidence (a person, a machine) can be counted only once per bid


def run_single(db, tender, entity, rule_type: str, p: dict, cutoff, used: set | None = None) -> Outcome:
    fn = SINGLE[rule_type]
    return fn(db, tender, entity, p, cutoff, used) if rule_type in USES_POOL else fn(db, tender, entity, p, cutoff)


def rule_order(rule: EligibilityRule) -> tuple:
    """Stricter personnel posts are filled first so one person is never counted for two posts."""
    if rule.rule_type == "personnel":
        p = rule.params
        lvl = QUAL_LEVELS.index(p["min_qualification"]) if p.get("min_qualification") in QUAL_LEVELS else -1
        return (1, -(try_D(p.get("min_experience_years")) or 0), -lvl, rule.id)
    return (0, 0, 0, rule.id)


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


def eval_for_jv(db, tender, jv: JV, rule: EligibilityRule, cutoff, cfg: dict | None = None,
                used: dict | None = None) -> list[tuple[str, Outcome]]:
    """JV evaluation per the rule's explicit 'jv' configuration; never assumes additivity.

    `cfg` overrides the stored configuration (the qualification planner passes the suggestion it is testing).
    `used` maps subject -> set of evidence ids already counted for another criterion (personnel, machines)."""
    cfg = cfg if cfg is not None else (rule.params.get("jv") or {})
    mode = cfg.get("mode")  # aggregate_sum / lead_only / each_member / any_member / member_scaled
    if not mode:
        return [("jv", Outcome(AMBIG, "The tender's JV rule for this criterion is not configured (how members combine).",
                               missing=["JV aggregation method for this criterion (from the JV clause)"]))]
    used = used if used is not None else {}
    p = rule.params
    rt = rule.rule_type
    entities = {m.legal_entity_id: db.get(LegalEntity, m.legal_entity_id) for m in jv.members}
    outs: list[tuple[str, Outcome]] = []
    if mode in ("lead_only", "each_member", "any_member"):
        targets = [m for m in jv.members if m.role == "lead"] if mode == "lead_only" else list(jv.members)
        res = [(m, run_single(db, tender, entities[m.legal_entity_id], rt, p, cutoff,
                              used.setdefault(f"entity:{m.legal_entity_id}", set()))) for m in targets]
        for m, o in res:
            outs.append((f"entity:{m.legal_entity_id}", o))
        if mode == "any_member":
            agg = PASS if any(o.result == PASS for _, o in res) else (INSUFF if any(o.result == INSUFF for _, o in res) else FAIL)
            outs.append(("jv:aggregate", Outcome(agg, f"Any one member must satisfy: {agg}.")))
        return outs
    lead_pct, mem_pct = try_D(cfg.get("lead_min_pct")), try_D(cfg.get("member_min_pct"))
    if mode == "member_scaled" and rt == "similar_work":
        for m in jv.members:
            pct = lead_pct if m.role == "lead" else mem_pct
            ent = entities[m.legal_entity_id]
            if pct is None:
                outs.append((f"entity:{m.legal_entity_id}", Outcome(NA, f"{ent.legal_name}: no individual similar-work share required.")))
                continue
            scaled = dict(p)
            if p.get("amount"):
                scaled["amount"] = str(q(D(p["amount"]) * pct / 100, 2))
            elif p.get("percent_of_estimate"):
                scaled["percent_of_estimate"] = str(q(D(p["percent_of_estimate"]) * pct / 100, 4))
            o = eval_similar_work(db, tender, ent, scaled, cutoff)
            o.explanation = f"{ent.legal_name} ({pct}% of the requirement): {o.explanation}"
            outs.append((f"entity:{m.legal_entity_id}", o))
        member_results = [o.result for _, o in outs if o.result != NA]
        agg = _worst(member_results) if member_results else AMBIG
        outs.append(("jv:aggregate", Outcome(agg, f"Each member meets its share of the similar-work requirement: {agg.replace('_', ' ')}.")))
        return outs
    if mode == "aggregate_sum" and rt in EXPERIENCE_RULES:
        # "Experience of all JV partners shall be aggregated": the works of every partner are pooled.
        for m in jv.members:
            ent = entities[m.legal_entity_id]
            o = _experience(db, tender, [ent], p, cutoff, rt)
            n = o.calculation.get("qualifying_count")
            outs.append((f"entity:{m.legal_entity_id}", Outcome(
                NA, f"{ent.legal_name}: contributes {n if n is not None else 'no'} qualifying work(s).",
                {"measure": str(n) if n is not None else None, "works": o.calculation.get("works", [])},
                o.missing, None, o.evidence_ids)))
        pooled = _experience(db, tender, [entities[m.legal_entity_id] for m in jv.members], p, cutoff, rt)
        pooled.explanation = f"JV combined: {pooled.explanation}"
        outs.append(("jv:aggregate", pooled))
        return outs
    if mode == "aggregate_sum" and rt in MULTI:
        fn = MULTI[rt]
        for m in jv.members:
            ent = entities[m.legal_entity_id]
            o = fn(db, tender, [ent], p, cutoff, None)
            pct = lead_pct if m.role == "lead" else mem_pct
            measure, thr = try_D(o.calculation.get("measure")), try_D(o.calculation.get("threshold"))
            if pct is not None and measure is not None and thr is not None:
                need = q(thr * pct / 100, 2)
                ok = measure >= need
                outs.append((f"entity:{m.legal_entity_id}", Outcome(
                    PASS if ok else (INSUFF if o.result == INSUFF else FAIL),
                    f"{ent.legal_name}: contributes {measure} vs individual minimum {pct}% = {need}.",
                    {"measure": str(measure), "minimum": str(need)}, o.missing, o.remedy, o.evidence_ids)))
            else:
                outs.append((f"entity:{m.legal_entity_id}", Outcome(
                    NA, f"{ent.legal_name}: contributes {measure if measure is not None else 'nothing yet'} (no individual minimum).",
                    {"measure": str(measure) if measure is not None else None}, o.missing, None, o.evidence_ids)))
        pooled = fn(db, tender, [entities[m.legal_entity_id] for m in jv.members], p, cutoff, used.setdefault("jv:aggregate", set()))
        pooled.explanation = f"JV combined: {pooled.explanation}"
        outs.append(("jv:aggregate", pooled))
        return outs
    if mode == "aggregate_sum" and rt == "net_worth":
        total, gaps = Decimal(0), False
        for m in jv.members:
            o = eval_net_worth(db, tender, entities[m.legal_entity_id], {"positive_only": True}, cutoff)
            nw = try_D(o.calculation.get("net_worth"))
            if nw is None:
                gaps = True
                outs.append((f"entity:{m.legal_entity_id}", o))
                continue
            total += nw
            outs.append((f"entity:{m.legal_entity_id}", Outcome(NA, f"{entities[m.legal_entity_id].legal_name}: net worth ₹{format_inr(nw)}.",
                                                                 {"measure": str(nw)}, evidence_ids=o.evidence_ids)))
        if gaps:
            outs.append(("jv:aggregate", Outcome(INSUFF, "Combined net worth cannot be computed: member evidence missing.")))
        else:
            need = Decimal(0) if p.get("positive_only") else D(p.get("min") or 0)
            ok = total > need if p.get("positive_only") else total >= need
            outs.append(("jv:aggregate", Outcome(PASS if ok else FAIL, f"Combined net worth ₹{format_inr(total)} (required "
                                                 f"{'positive' if p.get('positive_only') else '≥ ₹' + format_inr(need)}).",
                                                 {"aggregate": str(total)})))
        return outs
    if mode == "aggregate_sum" and rt == "avg_turnover":
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
    used: dict[str, set] = {}  # one person / machine is counted for one criterion only
    for rule in sorted(rules, key=rule_order):
        outs: list[tuple[str, Outcome]]
        if rule.rule_type.startswith("jv_"):
            outs = [("bidder", eval_jv_rule(rule, jv))]
        elif jv is None and entity is None:
            outs = [("bidder", Outcome(INSUFF, "No bidder entity or JV selected.", missing=["bidder"]))]
        elif cutoff is None:
            outs = [("bidder", Outcome(AMBIG, "Qualification cutoff date not confirmed; results cannot be computed "
                                               "against today's date.", missing=["qualification cutoff date"]))]
        elif jv is not None:
            outs = eval_for_jv(db, tender, jv, rule, cutoff, used=used)
        else:
            outs = [("bidder", run_single(db, tender, entity, rule.rule_type, rule.params, cutoff, used.setdefault("bidder", set())))]
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
