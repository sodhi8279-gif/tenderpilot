"""Order of precedence: which document governs when two base documents disagree.

Two steps, both under reviewer control:
1. `suggest_ranks` reads the tender's own "order of precedence" clause and proposes a rank per uploaded document
   from its type (SCC before GCC, and so on). A reviewer confirms or changes each rank (stored on the document).
2. `governing` uses the confirmed ranks to say which of a requirement's conflicting values governs. It is a
   suggestion shown beside the conflict; a reviewer applies it with one click (`apply`). Nothing changes by itself.

Amendments (corrigenda, addenda, pre-bid replies) are not ranked here: they change only the clauses they name and are
handled by the amendment flow. Lower rank number = higher priority (1 governs over 2).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Requirement, RequirementVersion, SourceDocument, SourcePage, Tender
from .requirements import current_version

# How each document type is named in precedence clauses (Indian SBD / CPWD / state PWD / MoHUA wording).
CLASS_PATTERNS: dict[str, re.Pattern] = {
    "scc": re.compile(r"special\s+conditions|particular\s+conditions|additional\s+conditions|\bSCC\b|\bPCC\b", re.I),
    "gcc": re.compile(r"general\s+conditions|\bGCC\b", re.I),
    "specification": re.compile(r"(?:technical\s+)?specifications?", re.I),
    "drawing": re.compile(r"drawings?", re.I),
    "boq": re.compile(r"bill\s+of\s+quantit|\bBOQ\b|\bBOQs\b|schedule\s+of\s+(?:rates|quantities)|price\s+(?:bid|schedule)|priced\s+schedule", re.I),
    "bds": re.compile(r"bid\s+data\s+sheet|tender\s+data\s+sheet|\bBDS\b|\bTDS\b", re.I),
    "itb": re.compile(r"instructions?\s+to\s+(?:bidders|tenderers)|\bITB\b|\bITT\b", re.I),
    "nit": re.compile(r"notice\s+inviting|\bNIT\b|\bNIB\b|invitation\s+for\s+bids", re.I),
    "forms": re.compile(r"(?:bid|contract)\s+forms|forms\s+of\s+(?:bid|contract)", re.I),
    "sbd": re.compile(r"standard\s+bid(?:ding)?\s+document|\bSBD\b|\bSPD\b", re.I),
}
AMENDMENT_CLASSES = ("corrigendum", "addendum", "prebid_reply")
_CLAUSE = re.compile(r"order\s+of\s+precedence|priority\s+of\s+(?:contract\s+)?documents", re.I)
WINDOW = 1600  # characters after the clause heading that hold the list


def _clause_text(db: Session, tender: Tender) -> tuple[str, dict] | None:
    """Text of the order-of-precedence list (clause heading + following lines, may run onto the next page)."""
    req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == "precedence.order"))
    cur = current_version(req) if req else None
    if cur is None or cur.anchor is None:
        return None
    a = cur.anchor
    pages = list(db.scalars(select(SourcePage).where(SourcePage.document_id == a.document_id,
                                                     SourcePage.page_index.in_([a.page_index, a.page_index + 1]))
                            .order_by(SourcePage.page_index)))
    text = "\n".join(p.text or "" for p in pages)
    m = _CLAUSE.search(text)
    if not m:
        return None
    doc = db.get(SourceDocument, a.document_id)
    return text[m.end(): m.end() + WINDOW], {"document": doc.filename if doc else None, "page": a.page_index + 1,
                                             "clause": a.clause_no, "requirement_id": req.id}


def order_from_text(text: str) -> list[str]:
    """Document types in the order they are first named in the clause text."""
    first: dict[str, int] = {}
    for cls, pat in CLASS_PATTERNS.items():
        m = pat.search(text)
        if m:
            first[cls] = m.start()
    # "Specifications" often appears inside "Special Conditions ... and Specifications": keep the earliest mention only.
    return sorted(first, key=first.get)


def suggest_ranks(db: Session, tender: Tender) -> dict:
    found = _clause_text(db, tender)
    docs = [d for d in db.scalars(select(SourceDocument).where(SourceDocument.tender_id == tender.id)
                                  .order_by(SourceDocument.id)) if d.status not in ("rejected", "container")]
    out_docs = []
    order: list[str] = []
    if found:
        order = order_from_text(found[0])
    for d in docs:
        amendment = d.is_amendment or d.doc_class in AMENDMENT_CLASSES
        suggested = None if amendment or d.doc_class not in order else order.index(d.doc_class) + 1
        out_docs.append({"id": d.id, "filename": d.filename, "doc_class": d.doc_class, "is_amendment": amendment,
                         "precedence_rank": d.precedence_rank, "suggested_rank": suggested})
    return {"clause_found": found is not None, "source": found[1] if found else None, "order": order,
            "documents": out_docs,
            "note": ("Ranks suggested from the tender's order-of-precedence clause. Confirm each one: 1 governs over 2."
                     if found else "No order-of-precedence clause has been read for this tender. Enter ranks from the "
                                   "contract conditions yourself (1 governs over 2).")}


def _doc_of(v: RequirementVersion, db: Session) -> SourceDocument | None:
    return db.get(SourceDocument, v.anchor.document_id) if v.anchor else None


def governing(db: Session, req: Requirement) -> dict | None:
    """For a requirement whose base documents disagree, which value governs by the confirmed precedence ranks."""
    if not req.has_conflict:
        return None
    cands = []
    for v in req.versions:
        if v.review_status == "rejected" or v.amendment_document_id or not v.anchor:
            continue
        d = _doc_of(v, db)
        if d is None or d.is_amendment:
            continue
        cands.append((v, d))
    docs = {d.id for _, d in cands}
    if len(docs) < 2:
        return None  # an amendment or a single-document change: not a precedence question
    missing = sorted({d.filename for _, d in cands if d.precedence_rank is None})
    if missing:
        return {"status": "ranks_missing", "documents_without_rank": missing,
                "message": "Record the precedence rank of " + ", ".join(missing) + " (Documents tab) to see which value governs."}
    best_rank = min(d.precedence_rank for _, d in cands)
    best = [(v, d) for v, d in cands if d.precedence_rank == best_rank]
    if len({d.id for _, d in best}) > 1:
        return {"status": "tie", "message": f"Documents with the same rank ({best_rank}) disagree; a reviewer must decide."}
    best_doc = best[0][1]
    v = max((v for v, _ in best), key=lambda x: x.version_no)  # latest reading from the governing document
    others = sorted({(d.filename, d.precedence_rank) for _, d in cands if d.id != best_doc.id}, key=lambda x: x[1])
    return {"status": "suggested", "version_no": v.version_no, "value": v.value, "document": best_doc.filename,
            "rank": best_rank, "overrides": [{"document": f, "rank": r} for f, r in others],
            "message": f"By the order of precedence, {best_doc.filename} (rank {best_rank}) governs over "
                       + ", ".join(f"{f} (rank {r})" for f, r in others) + "."}


def apply(db: Session, req: Requirement, user_id: int) -> RequirementVersion:
    """Reviewer accepts the precedence suggestion: the governing version becomes current and approved."""
    g = governing(db, req)
    if not g or g["status"] != "suggested":
        raise ValueError((g or {}).get("message") or "No precedence suggestion for this requirement")
    v = next(x for x in req.versions if x.version_no == g["version_no"])
    now = datetime.now(timezone.utc)
    for x in req.versions:
        x.is_current = x.version_no == v.version_no
    v.review_status, v.reviewer_id, v.reviewed_at = "approved", user_id, now
    v.review_note = ((v.review_note + " | ") if v.review_note else "") + "Applied by order of precedence: " + g["message"]
    req.current_version_no = v.version_no
    req.has_conflict = False
    return v
