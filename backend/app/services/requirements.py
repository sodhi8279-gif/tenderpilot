"""Requirement versioning, amendment (corrigendum) application and stale-output propagation."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import audit, notify
from ..models import (Approval, ChecklistTask, EligibilityRule, Evaluation, ExportPackage, GeneratedDocument,
                      QuoteVersion, Requirement, RequirementVersion, SourceAnchor, SourceDocument, Tender,
                      TenderVersion)
from .dates import IST
from .extract import Candidate


def current_version(req: Requirement) -> RequirementVersion | None:
    for v in req.versions:
        if v.is_current:
            return v
    return None


# Keys holding the text exactly as printed (or as OCR read it). They are kept for display but are not part of
# the value: "Rs..12,50,000" from a scan and "Rs. 12,50,000" from a Word file are the same EMD, not a conflict.
_PRESENTATION_KEYS = frozenset({"original"})


def same_value(a: dict | None, b: dict | None) -> bool:
    strip = lambda v: {k: x for k, x in (v or {}).items() if k not in _PRESENTATION_KEYS}
    return strip(a) == strip(b)


def _anchor(db: Session, doc: SourceDocument, c: Candidate, bbox, page_label) -> SourceAnchor:
    a = SourceAnchor(document_id=doc.id, doc_sha256=doc.sha256, doc_version=doc.version, page_index=c.page_index,
                     page_label=page_label, clause_no=c.clause_no, excerpt=c.excerpt[:600], bbox=bbox)
    db.add(a)
    db.flush()
    return a


def _machine_read(v: RequirementVersion) -> bool:
    return not str(v.extractor or "").startswith(("manual", "ai"))


def apply_candidates(db: Session, tender: Tender, doc: SourceDocument, cands: list[Candidate],
                     bboxes: dict[int, list | None], labels: dict[int, str | None], reread: bool = False) -> dict:
    """Idempotently upsert requirements from one document's candidates.

    `reread`: the same document is being read again by an improved reader. An unreviewed machine reading from this
    document is replaced; a value a person reviewed is kept and the new reading is offered beside it for comparison;
    unreviewed readings the new reader no longer finds are withdrawn. Nothing a person entered is overwritten."""
    created, amended, unchanged, conflicts, reread_updated, reread_offered, withdrawn = 0, 0, 0, 0, 0, 0, 0
    changed_req_ids: list[int] = []
    for i, c in enumerate(cands):
        req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == c.key))
        if req is None:
            req = Requirement(tender_id=tender.id, key=c.key, category=c.category, title=c.title, mandatory=c.mandatory)
            db.add(req)
            db.flush()
            a = _anchor(db, doc, c, bboxes.get(i), labels.get(c.page_index))
            db.add(RequirementVersion(requirement_id=req.id, version_no=1, tender_version=doc.effective_tender_version,
                                      nature=c.nature, value=c.value, value_text=c.excerpt, anchor_id=a.id,
                                      confidence=c.confidence, extractor=c.extractor, review_status="unreviewed", is_current=True,
                                      review_note="; ".join(c.notes) or None,
                                      amendment_document_id=doc.id if doc.is_amendment else None))
            created += 1
            continue
        db.refresh(req)
        # Idempotency: same document already contributed this exact value
        if any(v.anchor and v.anchor.document_id == doc.id and same_value(v.value, c.value) for v in req.versions):
            unchanged += 1
            continue
        cur = current_version(req)
        if cur is not None and same_value(cur.value, c.value):
            unchanged += 1
            continue
        a = _anchor(db, doc, c, bboxes.get(i), labels.get(c.page_index))
        next_no = max(v.version_no for v in req.versions) + 1
        if reread and not doc.is_amendment and cur is not None and cur.anchor and cur.anchor.document_id == doc.id:
            if cur.review_status == "unreviewed" and _machine_read(cur):
                cur.is_current = False
                db.add(RequirementVersion(requirement_id=req.id, version_no=next_no, tender_version=cur.tender_version,
                                          nature=c.nature, value=c.value, value_text=c.excerpt, anchor_id=a.id,
                                          confidence=c.confidence, extractor=c.extractor, review_status="unreviewed",
                                          is_current=True, review_note="; ".join(
                                              c.notes + [f"Re-read by the updated reader. Previous reading: {cur.value}"])[:2000]))
                req.current_version_no = next_no
                req.title, req.category = c.title, c.category
                reread_updated += 1
            else:
                db.add(RequirementVersion(requirement_id=req.id, version_no=next_no, tender_version=cur.tender_version,
                                          nature=c.nature, value=c.value, value_text=c.excerpt, anchor_id=a.id,
                                          confidence=c.confidence, extractor=c.extractor, review_status="needs_review",
                                          is_current=False,
                                          review_note="The updated reader reads this clause differently. The reviewed "
                                                      "value is kept; compare and correct it if needed."))
                reread_offered += 1
            changed_req_ids.append(req.id)
            continue
        if doc.is_amendment:
            # Amendment changes ONLY this clause; previous version retained as history.
            if cur is not None:
                cur.is_current = False
            db.add(RequirementVersion(requirement_id=req.id, version_no=next_no, tender_version=doc.effective_tender_version,
                                      nature=c.nature, value=c.value, value_text=c.excerpt, anchor_id=a.id,
                                      confidence=c.confidence, extractor=c.extractor, review_status="needs_review", is_current=True,
                                      amendment_document_id=doc.id,
                                      review_note=f"Amended by {doc.filename}. Previous value: {cur.value if cur else None}"))
            req.current_version_no = next_no
            req.has_conflict = True  # until a reviewer confirms the amendment's scope
            amended += 1
            changed_req_ids.append(req.id)
        else:
            # Two base documents disagree: keep both, mark material conflict for review.
            db.add(RequirementVersion(requirement_id=req.id, version_no=next_no, tender_version=doc.effective_tender_version,
                                      nature=c.nature, value=c.value, value_text=c.excerpt, anchor_id=a.id,
                                      confidence=c.confidence, extractor=c.extractor, review_status="needs_review", is_current=False,
                                      review_note=f"Conflicting value found in {doc.filename}"))
            req.has_conflict = True
            conflicts += 1
            changed_req_ids.append(req.id)
    if reread:
        found = {c.key for c in cands}
        for req in db.scalars(select(Requirement).where(Requirement.tender_id == tender.id)):
            cur = current_version(req)
            if (req.key not in found and cur is not None and cur.anchor and cur.anchor.document_id == doc.id
                    and cur.review_status == "unreviewed" and _machine_read(cur)):
                cur.review_status = "rejected"
                cur.review_note = ((cur.review_note or "") + " | Withdrawn: the updated reader no longer finds this "
                                   "requirement in the document.").strip(" |")
                withdrawn += 1
                changed_req_ids.append(req.id)
    if changed_req_ids:
        invalidate_dependents(db, tender, changed_req_ids,
                              reason=f"Requirements {'re-read from' if reread else 'changed by'} {doc.filename}")
    return {"created": created, "amended": amended, "unchanged": unchanged, "conflicts": conflicts,
            "reread_updated": reread_updated, "reread_offered": reread_offered, "withdrawn": withdrawn,
            "changed_requirement_ids": changed_req_ids}


def invalidate_dependents(db: Session, tender: Tender, req_ids: list[int], reason: str) -> dict:
    """Mark everything derived from these requirements stale; withdraw affected approvals."""
    rules = list(db.scalars(select(EligibilityRule).where(EligibilityRule.tender_id == tender.id,
                                                          EligibilityRule.requirement_id.in_(req_ids),
                                                          EligibilityRule.status.in_(["proposed", "approved"]))))
    rule_ids = [r.id for r in rules]
    for r in rules:
        r.status = "superseded"
    n_eval = 0
    if rule_ids:
        for ev in db.scalars(select(Evaluation).where(Evaluation.rule_id.in_(rule_ids), Evaluation.stale.is_(False))):
            ev.stale, ev.stale_reason = True, reason
            n_eval += 1
    n_tasks = 0
    for t in db.scalars(select(ChecklistTask).where(ChecklistTask.tender_id == tender.id, ChecklistTask.requirement_id.in_(req_ids))):
        t.stale = True
        t.history = [*t.history, {"at": datetime.now(IST).isoformat(), "event": "stale", "reason": reason}]
        n_tasks += 1
    reqs = {r.id: r for r in db.scalars(select(Requirement).where(Requirement.id.in_(req_ids)))}
    keys = {r.key for r in reqs.values()}
    n_docs = 0
    for g in db.scalars(select(GeneratedDocument).where(GeneratedDocument.tender_id == tender.id, GeneratedDocument.stale.is_(False))):
        used = set(g.upstream.get("requirement_keys", []))
        if used & keys or not used:
            g.stale = True
            n_docs += 1
    commercial = any(r.category in ("commercial",) for r in reqs.values())
    if commercial:
        for qv in db.scalars(select(QuoteVersion).where(QuoteVersion.tender_id == tender.id, QuoteVersion.status != "invalidated")):
            if qv.status in ("estimator_reviewed", "approved"):
                qv.status, qv.invalidated_reason = "invalidated", reason
    for ap in db.scalars(select(Approval).where(Approval.tender_id == tender.id, Approval.valid.is_(True),
                                                Approval.subject_type.in_(["package"] + (["quote"] if commercial else [])))):
        ap.valid, ap.invalidated_reason = False, reason
    for pk in db.scalars(select(ExportPackage).where(ExportPackage.tender_id == tender.id, ExportPackage.status.in_(["final_ready", "frozen"]))):
        pk.status = "superseded"
    audit(db, org_id=tender.org_id, user_id=None, action="dependents.invalidated", subject_type="tender",
          subject_id=tender.id, tender_id=tender.id, reason=reason, requirement_ids=req_ids, rules=len(rule_ids),
          evaluations=n_eval, tasks=n_tasks, documents=n_docs)
    notify(db, org_id=tender.org_id, tender_id=tender.id,
           message=f"{reason}: {n_eval} eligibility results, {n_tasks} tasks and {n_docs} documents need review again.")
    return {"rules": len(rule_ids), "evaluations": n_eval, "tasks": n_tasks, "documents": n_docs}


def bump_tender_version(db: Session, tender: Tender, reason: str, doc: SourceDocument) -> int:
    tender.current_version += 1
    db.add(TenderVersion(tender_id=tender.id, number=tender.current_version, reason=reason, source_document_id=doc.id))
    return tender.current_version


def sync_tender_facts(db: Session, tender: Tender) -> None:
    """Copy reviewed (or unreviewed-but-unique) deadline facts onto the tender for display."""
    req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == "deadline.bid_submission"))
    if req:
        v = current_version(req)
        if v and v.value.get("date"):
            from datetime import time as _t
            from zoneinfo import ZoneInfo
            tz = v.value.get("timezone") or "Asia/Kolkata"
            t = _t.fromisoformat(v.value["time"]) if v.value.get("time") else _t(23, 59)
            tender.deadline_at = datetime.combine(date.fromisoformat(v.value["date"]), t, tzinfo=ZoneInfo(tz))
            tender.deadline_tz = tz
            tender.deadline_raw = v.value.get("original")
            tender.deadline_source = f"Requirement {req.key} v{v.version_no} ({v.review_status})"
