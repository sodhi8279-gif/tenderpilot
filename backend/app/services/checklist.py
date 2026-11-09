"""Clause-linked dynamic checklist. Idempotent: tasks are keyed and never duplicated."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import JV, ChecklistTask, LegalEntity, Requirement, Tender
from .requirements import current_version

# requirement key prefix -> (group, action_type, evidence_category, certification, envelope, signing)
TASK_SPECS: dict[str, tuple] = {
    "fin.avg_turnover": ("financial_qualification", "obtain_external", "turnover_certificate", "CA", "technical", False),
    "fin.net_worth": ("financial_qualification", "obtain_external", "net_worth", "CA", "technical", False),
    "fin.solvency": ("financial_qualification", "obtain_external", "solvency", "bank", "technical", False),
    "fin.bid_capacity": ("financial_qualification", "draft_internal", "existing_commitments", "CA", "technical", True),
    "exp.similar_work": ("bidder", "upload_existing", "completion_certificate", None, "technical", False),
    "commercial.emd": ("fee_security", "obtain_external", "emd_proof", "bank", "fee_emd", False),
    "commercial.tender_fee": ("fee_security", "upload_existing", "fee_proof", None, "fee_emd", False),
    "doc.poa": ("bidder", "notarize", "power_of_attorney", "notary", "technical", True),
    "doc.affidavit": ("bidder", "print_sign_scan", "declaration", "notary", "technical", True),
    "doc.blacklisting": ("bidder", "draft_internal", "declaration", None, "technical", True),
    "doc.oem_authorization": ("specialist", "obtain_external", "oem_authorization", "OEM", "technical", False),
}


def _spec(key: str):
    for prefix, spec in TASK_SPECS.items():
        if key == prefix or key.startswith(prefix + "."):
            return spec
    if key.startswith("doc."):
        return ("bidder", "upload_existing", None, None, "technical", False)
    return None


def _question(req: Requirement, excerpt: str, clause: str | None, page: int | None, entity_name: str) -> str:
    where = f"clause {clause}" if clause else (f"page {page + 1}" if page is not None else "the tender")
    k = req.key
    if k.startswith("exp.similar_work"):
        return (f"Upload completion certificates for similar works by {entity_name} showing the completion date, executed "
                f"value and employer; required by {where}.")
    if k == "fin.avg_turnover":
        return f"Upload CA-certified annual turnover for {entity_name} for each required financial year; required by {where}."
    if k == "commercial.emd":
        return f"Upload proof of EMD / bid security (BG, FDR or online payment receipt) as specified in {where}."
    return f"Provide '{req.title}' for {entity_name}; required by {where}."


def generate_checklist(db: Session, tender: Tender) -> int:
    entities: list[tuple[str, LegalEntity]] = []
    if tender.bidder_type == "jv" and tender.jv_id:
        jv = db.get(JV, tender.jv_id)
        for m in jv.members:
            entities.append((f"entity{m.legal_entity_id}", db.get(LegalEntity, m.legal_entity_id)))
    elif tender.bidder_entity_id:
        entities.append((f"entity{tender.bidder_entity_id}", db.get(LegalEntity, tender.bidder_entity_id)))
    else:
        entities.append(("bidder", None))
    created = 0
    existing = {t.task_key: t for t in db.scalars(select(ChecklistTask).where(ChecklistTask.tender_id == tender.id))}
    for req in db.scalars(select(Requirement).where(Requirement.tender_id == tender.id)):
        v = current_version(req)
        if v is None or v.review_status == "rejected":
            continue
        spec = _spec(req.key)
        if spec is None:
            continue
        group, action, evcat, cert, envelope, signing = spec
        if evcat is None:
            evcat = (v.value or {}).get("evidence_category")
        targets = entities if group in ("bidder", "financial_qualification") else [("bidder", None)]
        for suffix, ent in targets:
            key = f"{req.key}:{suffix}"
            if key in existing:
                t = existing[key]
                if t.stale and v.review_status == "approved":
                    t.stale = False  # requirement re-reviewed after amendment
                continue
            name = ent.legal_name if ent else "the bidder"
            anchor = v.anchor
            db.add(ChecklistTask(
                tender_id=tender.id, task_key=key, requirement_id=req.id,
                title=f"{req.title}" + (f" — {name}" if ent and len(targets) > 1 else ""),
                question=_question(req, v.value_text, anchor.clause_no if anchor else None,
                                   anchor.page_index if anchor else None, name),
                group="jv_member" if len(targets) > 1 and ent else group, action_type=action, mandatory=req.mandatory,
                evidence_category=evcat, certification_required=cert, signing_required=signing, envelope=envelope,
                history=[{"event": "created", "from_requirement": req.key, "version": v.version_no}]))
            created += 1
    # Always-required submission tasks
    for key, title, action, env in (
        ("sys.sign_forms", "Sign/certify generated forms with authorised signatory (DSC/physical as required)", "print_sign_scan", "technical"),
        ("sys.portal_upload", "Upload files to the portal slots and collect the acknowledgement", "portal_action", "all"),
    ):
        if key not in existing:
            db.add(ChecklistTask(tender_id=tender.id, task_key=key, title=title, action_type=action, envelope=env,
                                 # portal upload is proven by the receipt record, so it cannot block preflight
                                 group="submission", mandatory=key == "sys.sign_forms",
                                 signing_required=key == "sys.sign_forms"))
            created += 1
    db.flush()
    return created
