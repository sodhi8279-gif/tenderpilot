"""Client firms: the library a bid-submission company keeps for the firms it bids for.

Each firm (a LegalEntity) carries a profile and credentials (EvidenceRecords) grouped for the screens. At tender time
the firm, or the JV members, are chosen from this library; eligibility and forms read the same records, so nothing is
typed again per tender.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import JV, EvidenceRecord, JVMember, LegalEntity, Tender
from .money import try_D

# Credential groups shown as tabs on a firm's page (categories stay those the eligibility engine reads).
GROUPS: dict[str, dict] = {
    "financial": {"label": "Financials", "categories": ["audited_accounts", "turnover_certificate", "net_worth", "solvency",
                                                        "bank_facility", "existing_commitments"]},
    "experience": {"label": "Work experience", "categories": ["completion_certificate", "performance_certificate", "work_order",
                                                              "agreement", "executed_quantity", "project_scope"]},
    "machinery": {"label": "Machinery", "categories": ["machinery"]},
    "personnel": {"label": "Personnel", "categories": ["personnel"]},
    "registrations": {"label": "Registrations & certificates", "categories": [
        "incorporation", "registration", "pan", "gst", "quality_certification", "power_of_attorney", "oem_authorization",
        "declaration", "datasheet"]},
    "other": {"label": "Other documents", "categories": ["emd_proof", "fee_proof", "other"]},
}
CATEGORY_GROUP = {c: g for g, d in GROUPS.items() for c in d["categories"]}

# Firm profile fields (stored in LegalEntity.profile) with labels; core identity fields are columns.
PROFILE_FIELDS: dict[str, str] = {
    "short_name": "Short name", "incorporation_date": "Date of incorporation", "cin": "CIN / LLPIN / registration no.",
    "tan": "TAN", "udyam": "Udyam (MSME) registration no.", "msme_category": "MSME category", "epf": "EPF code",
    "esi": "ESI code", "labour_licence": "Labour licence no.", "email": "E-mail", "phone": "Phone",
    "contact_person": "Contact person", "contact_mobile": "Contact mobile", "website": "Website",
    "bank_name": "Bank (EMD refunds)", "bank_branch": "Bank branch", "bank_account": "Account no.", "bank_ifsc": "IFSC",
    "notes": "Notes",
}
CORE_REQUIRED = {"registered_address": "Registered address", "pan": "PAN", "gstin": "GSTIN",
                 "signatory_name": "Authorised signatory", "signatory_designation": "Signatory designation"}
PROFILE_REQUIRED = ("email", "phone")
AGENCY_FIELDS: dict[str, str] = {
    "name": "Company name", "address": "Address", "phone": "Phone", "email": "E-mail", "gstin": "GSTIN", "pan": "PAN",
    "website": "Website", "contact_person": "Contact person",
}


def clean_profile(data: dict | None, allowed: dict[str, str]) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if k in allowed and v is not None:
            s = str(v).strip()[:500]
            if s:
                out[k] = s
    return out


def missing_profile(e: LegalEntity) -> list[str]:
    miss = [label for f, label in CORE_REQUIRED.items() if not getattr(e, f)]
    prof = e.profile or {}
    return miss + [PROFILE_FIELDS[k] for k in PROFILE_REQUIRED if not prof.get(k)]


def _current(db: Session, e: LegalEntity) -> list[EvidenceRecord]:
    return list(db.scalars(select(EvidenceRecord).where(EvidenceRecord.legal_entity_id == e.id,
                                                        EvidenceRecord.org_id == e.org_id, EvidenceRecord.is_current)))


def tenders_for(db: Session, org_id: int, entity_id: int) -> list[dict]:
    jv_ids = {m.jv_id: m for m in db.scalars(select(JVMember).where(JVMember.legal_entity_id == entity_id))}
    q = select(Tender).where(Tender.org_id == org_id, or_(Tender.bidder_entity_id == entity_id,
                                                          Tender.jv_id.in_(list(jv_ids) or [-1])))
    out = []
    for t in db.scalars(q.order_by(Tender.id.desc())):
        if t.bidder_type == "jv" and t.jv_id in jv_ids:
            m = jv_ids[t.jv_id]
            role = f"JV {'lead' if m.role == 'lead' else 'member'} ({m.share_pct}%)"
        elif t.bidder_type == "entity" and t.bidder_entity_id == entity_id:
            role = "Single bidder"
        else:
            continue
        out.append({"id": t.id, "title": t.title, "tender_ref": t.tender_ref, "department": t.department, "role": role,
                    "status": t.status, "deadline_at": t.deadline_at.isoformat() if t.deadline_at else None})
    return out


def summary(db: Session, e: LegalEntity, today: date | None = None, with_tenders: bool = True) -> dict:
    """What a firm brings to a tender, at a glance: profile gaps, credentials by group, expiries, key figures."""
    today = today or date.today()
    recs = _current(db, e)
    counts = {g: 0 for g in GROUPS}
    unverified = expired = expiring = 0
    expiring_list = []
    turnover: dict[str, Decimal] = {}
    net_worth = None
    works, works_value = 0, Decimal(0)
    machines, persons = Decimal(0), 0
    for r in recs:
        if r.verification_state == "rejected":
            continue
        counts[CATEGORY_GROUP.get(r.category, "other")] += 1
        if r.verification_state != "verified":
            unverified += 1
        if r.expiry_date:
            if r.expiry_date < today:
                expired += 1
                expiring_list.append({"id": r.id, "title": r.title, "expiry_date": r.expiry_date.isoformat(), "expired": True})
            elif r.expiry_date <= today + timedelta(days=30):
                expiring += 1
                expiring_list.append({"id": r.id, "title": r.title, "expiry_date": r.expiry_date.isoformat(), "expired": False})
        f = r.fields or {}
        if r.category in ("turnover_certificate", "audited_accounts") and f.get("fy"):
            amt = try_D(f.get("turnover") or f.get("amount"))
            if amt is not None:
                turnover[str(f["fy"])] = max(turnover.get(str(f["fy"]), Decimal(0)), amt)
        if r.category in ("net_worth", "audited_accounts"):
            nw = try_D(f.get("net_worth") or (f.get("amount") if r.category == "net_worth" else None))
            if nw is not None:
                net_worth = {"amount": str(nw), "as_of": f.get("as_of") or f.get("fy")}
        if r.category in ("completion_certificate", "performance_certificate"):
            works += 1
            works_value += try_D(f.get("executed_value")) or Decimal(0)
        if r.category == "machinery":
            machines += try_D(f.get("quantity")) or Decimal(1)
        if r.category == "personnel":
            persons += 1
    fys = sorted(turnover, reverse=True)[:5]
    out = {"profile_missing": missing_profile(e), "counts": counts, "total": sum(counts.values()), "unverified": unverified,
           "expired": expired, "expiring_30_days": expiring, "expiring": sorted(expiring_list, key=lambda x: x["expiry_date"]),
           "turnover_by_fy": [{"fy": fy, "amount": str(turnover[fy])} for fy in fys], "net_worth": net_worth,
           "works": {"count": works, "total_value": str(works_value)}, "machinery_units": str(machines), "personnel": persons}
    if with_tenders:
        out["tenders"] = tenders_for(db, e.org_id, e.id)
    return out


def participants(db: Session, t: Tender) -> dict:
    """The firm or JV members bidding this tender, each with its credential summary."""
    firms = []
    jv = db.get(JV, t.jv_id) if t.bidder_type == "jv" and t.jv_id else None
    if jv:
        for m in sorted(jv.members, key=lambda m: (m.role != "lead", m.id)):
            e = db.get(LegalEntity, m.legal_entity_id)
            firms.append({"id": e.id, "legal_name": e.legal_name, "role": m.role, "share_pct": str(m.share_pct),
                          "summary": summary(db, e, with_tenders=False)})
    elif t.bidder_entity_id:
        e = db.get(LegalEntity, t.bidder_entity_id)
        firms.append({"id": e.id, "legal_name": e.legal_name, "role": "bidder", "share_pct": "100",
                      "summary": summary(db, e, with_tenders=False)})
    return {"mode": "jv" if jv else ("single" if firms else None),
            "jv": {"id": jv.id, "name": jv.name, "tender_specific": jv.tender_id == t.id} if jv else None, "firms": firms}
