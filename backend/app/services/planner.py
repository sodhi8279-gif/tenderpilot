"""Qualification planner: how can this tender be qualified, by one company alone or by JV partners together?

For every criterion of the tender (financial, similar work, physical quantities, key personnel, plant & machinery,
registration, documents) the planner
  1. tests each of the organization's legal entities on its own (coverage matrix), then
  2. tests every single-entity bid and every JV combination the tender allows (lead choice, minimum shares),
     using the confirmed JV sharing rule for each criterion, or the one suggested from the JV clause
     (clearly marked as an assumption), and
  3. ranks the options and lists, for each, what still has to be obtained.

It is read-only and uses the same deterministic evaluators as the eligibility matrix, on verified evidence only.
It is a planning aid, never a statement that a bid will qualify.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_UP, Decimal
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import EligibilityRule, EvidenceRecord, LegalEntity, Tender
from .eligibility import (AMBIG, FAIL, INSUFF, NA, PASS, Outcome, _CACHE, _worst, eval_for_jv, eval_jv_rule, rule_order,
                          run_single, suggest_jv)
from .money import D, try_D

MAX_ENTITIES = 8
MAX_JV_SIZE = 3
MAX_OPTIONS = 12
RANK = {PASS: 0, NA: 0, AMBIG: 1, INSUFF: 2, FAIL: 3}


@dataclass
class _Member:
    legal_entity_id: int
    role: str
    share_pct: Decimal


@dataclass
class _PlanJV:
    members: list[_Member] = field(default_factory=list)


def _default_cfg(rule_type: str) -> dict:
    if rule_type in ("document_present", "registration_class"):
        return {"mode": "each_member"}
    if rule_type in ("similar_work", "specific_experience"):
        return {"mode": "any_member"}
    if rule_type in ("solvency", "bid_capacity"):
        return {"mode": "lead_only"}
    return {"mode": "aggregate_sum"}


def jv_config(rule: EligibilityRule, split: dict | None) -> tuple[dict, str]:
    """(config, source) where source is 'confirmed', 'suggested' (from the JV clause) or 'assumed'."""
    if (rule.params.get("jv") or {}).get("mode"):
        return rule.params["jv"], "confirmed"
    sugg = rule.params.get("jv_suggested") or suggest_jv(rule.rule_type, split)
    if sugg and sugg.get("mode"):
        return sugg, "suggested"
    return _default_cfg(rule.rule_type), "assumed"


MODE_TEXT = {"aggregate_sum": "members' figures added", "lead_only": "lead member alone", "each_member": "every member",
             "any_member": "any one member", "member_scaled": "each member meets its % share"}


def _cfg_text(cfg: dict) -> str:
    t = MODE_TEXT.get(cfg.get("mode"), cfg.get("mode") or "?")
    extra = []
    if cfg.get("lead_min_pct"):
        extra.append(f"lead ≥ {cfg['lead_min_pct']}%")
    if cfg.get("member_min_pct"):
        extra.append(f"others ≥ {cfg['member_min_pct']}%")
    return t + (f" ({', '.join(extra)})" if extra else "")


def _shares(n: int, lead_min: Decimal | None) -> list[Decimal]:
    """Lead share first: the tender minimum (or an even split if higher), the rest shared evenly."""
    even = (Decimal(100) / n).quantize(Decimal("1"), rounding=ROUND_UP)
    lead = max(lead_min or Decimal(0), even)
    lead = min(lead, Decimal(100) - (n - 1))  # leave at least 1% per other member
    rest = Decimal(100) - lead
    others = [(rest / (n - 1)).quantize(Decimal("0.01")) for _ in range(n - 1)]
    others[-1] += rest - sum(others)
    return [lead] + others


def _decisive(outs: list[tuple[str, Outcome]]) -> str:
    subj = dict(outs)
    head = [o.result for s, o in outs if s in ("bidder", "jv:aggregate", "jv")]
    member_fail = [o.result for s, o in outs if s.startswith("entity:") and o.result == FAIL]
    if head:
        return _worst(head + member_fail)
    return _worst([o.result for o in subj.values()])


def _short(o: Outcome) -> dict:
    return {"result": o.result, "explanation": o.explanation, "missing": o.missing, "remedy": o.remedy,
            "measure": o.calculation.get("measure"), "threshold": o.calculation.get("threshold")}


def _criteria(rules: list[EligibilityRule]) -> list[dict]:
    """Group similar-work alternatives (e.g. 3 × 40% OR 2 × 60% OR 1 × 80%) into one criterion."""
    out, sim = [], []
    for r in sorted(rules, key=rule_order):
        if r.rule_type == "similar_work" and r.params.get("group") == "similar_work":
            sim.append(r)
        else:
            out.append({"key": f"rule:{r.id}", "rules": [r]})
    if sim:
        out.insert(0, {"key": "group:similar_work", "rules": sim})
    return out


def _combine_alternatives(results: list[str]) -> str:
    """Any one alternative passing is enough."""
    for r in (PASS, INSUFF, AMBIG, FAIL):
        if r in results:
            return r
    return NA


def _label(r: EligibilityRule) -> str:
    return {"avg_turnover": "Financial", "net_worth": "Financial", "solvency": "Financial", "bid_capacity": "Financial",
            "similar_work": "General experience" if r.params.get("experience_type") == "general" else "Similar work",
            "specific_experience": "Specific experience", "physical_quantity": "Physical quantity", "personnel": "Key personnel",
            "equipment": "Plant & machinery", "registration_class": "Registration", "document_present": "Document"}.get(r.rule_type, r.rule_type)


def _entities(db: Session, tender: Tender) -> tuple[list[LegalEntity], list[str]]:
    ents = list(db.scalars(select(LegalEntity).where(LegalEntity.org_id == tender.org_id).order_by(LegalEntity.id)))
    notes = []
    if len(ents) > MAX_ENTITIES:
        counts = dict(db.execute(select(EvidenceRecord.legal_entity_id, func.count()).where(
            EvidenceRecord.org_id == tender.org_id, EvidenceRecord.verification_state == "verified",
            EvidenceRecord.is_current.is_(True)).group_by(EvidenceRecord.legal_entity_id)).all())
        keep = {tender.bidder_entity_id} - {None}
        ranked = sorted(ents, key=lambda e: (e.id not in keep, -counts.get(e.id, 0), e.id))
        notes.append(f"{len(ents)} legal entities on file; the planner compares the {MAX_ENTITIES} with the most verified records.")
        ents = sorted(ranked[:MAX_ENTITIES], key=lambda e: e.id)
    return ents, notes


def plan(db: Session, tender: Tender) -> dict:
    rules = list(db.scalars(select(EligibilityRule).where(EligibilityRule.tender_id == tender.id,
                                                          EligibilityRule.status.in_(["proposed", "approved"]))))
    crit_rules = [r for r in rules if not r.rule_type.startswith("jv_")]
    jv_rules = {r.rule_type: r for r in rules if r.rule_type.startswith("jv_")}
    assumptions: list[str] = []
    cutoff = tender.qualification_cutoff
    if cutoff is None:
        cutoff = tender.deadline_at.date() if tender.deadline_at else date.today()
        assumptions.append(f"Qualification cutoff not confirmed; {cutoff.isoformat()} "
                           f"({'bid submission date' if tender.deadline_at else 'today'}) is used for this plan only.")
    entities, notes = _entities(db, tender)
    assumptions += notes
    if not crit_rules:
        return {"criteria": [], "entities": [], "options": [], "assumptions": assumptions, "jv": {},
                "message": "No qualification rules yet. Upload the NIT so the criteria can be read, then plan."}
    unapproved = sum(1 for r in crit_rules if r.status != "approved")
    if unapproved:
        assumptions.append(f"{unapproved} rule(s) still await reviewer confirmation; their values are as extracted.")
    from .eligibility import _requirement_value
    split = _requirement_value(db, tender, "jv.criteria_split")
    permitted = jv_rules["jv_permitted"].params.get("permitted") if "jv_permitted" in jv_rules else None
    max_members = int(jv_rules["jv_max_members"].params.get("max_members") or 0) if "jv_max_members" in jv_rules else None
    lead_min = try_D(jv_rules["jv_lead_share"].params.get("min_percent")) if "jv_lead_share" in jv_rules else None
    if permitted is None:
        assumptions.append("The tender's JV clause was not found; JV options are shown in case a JV is allowed.")
    if max_members is None and permitted is not False:
        assumptions.append(f"Maximum JV members not stated; combinations of up to {MAX_JV_SIZE} are tried.")
    groups = _criteria(crit_rules)
    cfgs = {r.id: jv_config(r, split) for r in crit_rules}
    ent_name = {e.id: e.legal_name for e in entities}

    token = _CACHE.set({})
    try:
        # 1. coverage matrix: each criterion, each entity alone
        criteria = []
        for g in groups:
            rs = g["rules"]
            cells = {}
            for e in entities:
                outs = [run_single(db, tender, e, r.rule_type, r.params, cutoff, None) for r in rs]
                res = _combine_alternatives([o.result for o in outs]) if len(rs) > 1 else outs[0].result
                best = next((o for o in outs if o.result == res), outs[0])
                cells[str(e.id)] = _short(best) | {"result": res}
            r0 = rs[0]
            cfg, src = cfgs[r0.id]
            req = None
            if r0.requirement_id:
                from ..models import Requirement
                from .requirements import current_version
                rq = db.get(Requirement, r0.requirement_id)
                v = current_version(rq) if rq else None
                req = {"clause_no": v.anchor.clause_no if v and v.anchor else None,
                       "page_index": v.anchor.page_index if v and v.anchor else None} if v else None
            criteria.append({"key": g["key"], "label": _label(r0),
                             "text": " OR ".join(r.explanation for r in rs) if len(rs) > 1 else r0.explanation,
                             "rule_ids": [r.id for r in rs], "status": "approved" if all(r.status == "approved" for r in rs) else "proposed",
                             "source": req, "jv_rule": _cfg_text(cfg), "jv_rule_source": src,
                             "jv_rule_basis": cfg.get("basis"), "cells": cells,
                             "met_alone_by": [ent_name[int(k)] for k, c in cells.items() if c["result"] == PASS]})

        def evaluate_option(jv: _PlanJV | None, entity: LegalEntity | None) -> dict:
            used: dict = {}
            rows, assumed = [], 0
            for g in groups:
                alt = []
                for r in g["rules"]:
                    if jv is None:
                        o = run_single(db, tender, entity, r.rule_type, r.params, cutoff, used.setdefault("bidder", set()))
                        alt.append((o.result, [("bidder", o)], None))
                    else:
                        cfg, src = cfgs[r.id]
                        outs = eval_for_jv(db, tender, jv, r, cutoff, cfg=cfg, used=used)
                        alt.append((_decisive(outs), outs, src))
                res = _combine_alternatives([a[0] for a in alt]) if len(alt) > 1 else alt[0][0]
                chosen = next(a for a in alt if a[0] == res)
                src = chosen[2]
                if src and src != "confirmed":
                    assumed += 1
                head = next((o for s, o in chosen[1] if s in ("bidder", "jv:aggregate", "jv")), chosen[1][0][1])
                contributions = [{"entity": ent_name.get(int(s.split(":")[1]), s), "result": o.result,
                                  "explanation": o.explanation,
                                  "measure": o.calculation.get("measure") or o.calculation.get("average")}
                                 for s, o in chosen[1] if s.startswith("entity:")]
                missing = sorted({m for _, o in chosen[1] for m in o.missing})
                remedies = sorted({o.remedy for _, o in chosen[1] if o.remedy and o.result != PASS})
                rows.append({"criterion": g["key"], "result": res, "explanation": head.explanation,
                             "contributions": contributions, "missing": missing, "remedies": remedies,
                             "jv_rule_source": src})
            if jv is not None:
                for jr in jv_rules.values():
                    o = eval_jv_rule(jr, jv)  # permitted / max members / lead share, with the proposed shares
                    rows.append({"criterion": f"rule:{jr.id}", "result": o.result, "explanation": o.explanation,
                                 "contributions": [], "missing": o.missing, "remedies": [], "jv_rule_source": "confirmed"})
            counts = {k: sum(1 for x in rows if x["result"] == k) for k in (PASS, FAIL, INSUFF, AMBIG, NA)}
            if counts[FAIL]:
                status = "does_not_qualify"
            elif counts[INSUFF] or counts[AMBIG]:
                status = "gaps_to_close"
            elif assumed or unapproved or assumptions:
                status = "qualifies_subject_to_confirmation"
            else:
                status = "qualifies"
            todo = []
            for x in rows:
                if x["result"] in (FAIL, INSUFF, AMBIG):
                    weak = [c["explanation"] for c in x["contributions"] if c["result"] in (FAIL, INSUFF, AMBIG)]
                    todo.extend(x["missing"] or weak or [x["explanation"]])
            return {"rows": rows, "counts": counts, "status": status, "assumed_jv_rules": assumed,
                    "todo": list(dict.fromkeys(todo))[:12]}

        options = []
        for e in entities:
            ev = evaluate_option(None, e)
            options.append({"kind": "single", "title": f"{e.legal_name} alone", "members": [
                {"legal_entity_id": e.id, "legal_name": e.legal_name, "role": "bidder", "share_pct": "100"}], **ev})
        jv_note = None
        if permitted is False:
            jv_note = "The tender does not permit joint ventures."
        elif len(entities) < 2:
            jv_note = "Add the prospective partner as a legal entity (with its verified records) to test JV options."
        else:
            top = min(max_members or MAX_JV_SIZE, MAX_JV_SIZE, len(entities))
            for size in range(2, top + 1):
                for combo in combinations(entities, size):
                    for lead in combo:
                        shares = _shares(size, lead_min)
                        ordered = [lead] + [e for e in combo if e is not lead]
                        jv = _PlanJV([_Member(e.id, "lead" if i == 0 else "member", shares[i]) for i, e in enumerate(ordered)])
                        ev = evaluate_option(jv, None)
                        options.append({"kind": "jv", "title": " + ".join(
                            f"{e.legal_name}{' (lead)' if i == 0 else ''}" for i, e in enumerate(ordered)),
                            "members": [{"legal_entity_id": e.id, "legal_name": e.legal_name,
                                         "role": "lead" if i == 0 else "member", "share_pct": str(shares[i])}
                                        for i, e in enumerate(ordered)], **ev})
    finally:
        _CACHE.reset(token)

    order = {"qualifies": 0, "qualifies_subject_to_confirmation": 1, "gaps_to_close": 2, "does_not_qualify": 3}
    options.sort(key=lambda o: (order[o["status"]], o["counts"][FAIL], o["counts"][INSUFF] + o["counts"][AMBIG],
                                o["assumed_jv_rules"], len(o["members"]), -o["counts"][PASS]))
    best = options[0] if options else None
    singles_ok = [o for o in options if o["kind"] == "single" and o["status"] in ("qualifies", "qualifies_subject_to_confirmation")]
    if singles_ok:
        headline = f"{singles_ok[0]['members'][0]['legal_name']} can meet every criterion alone on the verified records."
    elif best and best["kind"] == "jv" and best["status"] in ("qualifies", "qualifies_subject_to_confirmation"):
        headline = f"No single company qualifies alone; the JV {best['title']} meets every criterion."
    elif best:
        headline = f"No option meets every criterion yet. Closest: {best['title']} ({best['counts'][FAIL]} failing, " \
                   f"{best['counts'][INSUFF] + best['counts'][AMBIG]} open)."
    else:
        headline = "No options to compare."
    return {"cutoff": cutoff.isoformat(), "assumptions": assumptions, "headline": headline,
            "entities": [{"id": e.id, "legal_name": e.legal_name} for e in entities], "criteria": criteria,
            "jv": {"permitted": permitted, "max_members": max_members, "lead_min_pct": str(lead_min) if lead_min else None,
                   "split": split, "note": jv_note},
            "options": options[:MAX_OPTIONS], "options_evaluated": len(options),
            "disclaimer": "Planning aid based on verified records and the rules as read from the tender. Confirm every rule, "
                          "and the JV sharing clause, before relying on a result. Not an assurance of qualification."}
