"""Document studio: DOCX generation from built-in or tender-supplied templates + PDF preview.

* Tender-supplied DOCX forms are filled in place ({{placeholder}} or [placeholder] / dotted blanks are
  mapped by a reviewer). Original paragraphs, numbering and tables are preserved.
* Missing values render as visible «MISSING: field» markers and block final export.
* Nothing is ever marked signed: generated documents start as Draft / Awaiting Signature.
* Price fields cannot be mapped into technical-envelope documents.
"""
from __future__ import annotations

import io
import re
from datetime import date

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import JV, EvidenceRecord, LegalEntity, Requirement, Tender
from .money import amount_in_words, format_inr, try_D
from .requirements import current_version

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.\- ]+?)\s*\}\}")
MISSING = "«MISSING: {}»"
PRICE_PATHS = ("quote.",)

FIELD_CATALOG = {
    "tender.title": "Tender title", "tender.tender_ref": "Tender reference / ID", "tender.department": "Department / employer",
    "tender.deadline": "Bid submission deadline", "entity.legal_name": "Bidder legal name",
    "entity.registered_address": "Registered address", "entity.signatory_name": "Authorised signatory",
    "entity.signatory_designation": "Signatory designation", "entity.pan": "PAN", "entity.gstin": "GSTIN",
    "entity.entity_type": "Constitution of bidder", "jv.name": "JV name", "jv.members": "JV members and shares",
    "req.commercial.emd": "EMD amount", "req.commercial.bid_validity": "Bid validity (days)",
    "req.commercial.completion_period": "Completion period", "letter.date": "Letter date",
    "quote.total": "Quoted total (financial envelope only)", "quote.total_words": "Quoted total in words (financial only)",
}

BUILTINS = {
    "cover_letter": ("Covering letter", "technical"),
    "bid_form": ("Letter of bid / bid form (non-price)", "technical"),
    "experience_list": ("Schedule of similar works executed", "technical"),
    "undertaking": ("Undertaking / declaration (non-blacklisting, correctness)", "technical"),
    "poa_draft": ("Power of attorney — DRAFT for legal review", "technical"),
    "jv_agreement_draft": ("JV agreement — DRAFT for legal review", "technical"),
    "clarification_request": ("Pre-bid clarification request", "internal"),
    "financial_bid_letter": ("Financial bid covering letter (price)", "financial"),
}


def build_context(db: Session, tender: Tender, quote=None) -> dict[str, str | None]:
    ctx: dict[str, str | None] = {k: None for k in FIELD_CATALOG}
    ctx.update({"tender.title": tender.title or None, "tender.tender_ref": tender.tender_ref or None,
                "tender.department": tender.department or None, "letter.date": date.today().strftime("%d-%m-%Y")})
    if tender.deadline_at:
        ctx["tender.deadline"] = tender.deadline_at.strftime("%d-%m-%Y %H:%M") + f" ({tender.deadline_tz})"
    ent = db.get(LegalEntity, tender.bidder_entity_id) if tender.bidder_entity_id else None
    jv = db.get(JV, tender.jv_id) if tender.bidder_type == "jv" and tender.jv_id else None
    if jv:
        lead = next((m for m in jv.members if m.role == "lead"), None)
        ent = db.get(LegalEntity, lead.legal_entity_id) if lead else ent
        ctx["jv.name"] = jv.name
        ctx["jv.members"] = "; ".join(f"{db.get(LegalEntity, m.legal_entity_id).legal_name} ({m.role}, {m.share_pct}%)" for m in jv.members)
    if ent:
        for f in ("legal_name", "registered_address", "signatory_name", "signatory_designation", "pan", "gstin", "entity_type"):
            ctx[f"entity.{f}"] = getattr(ent, f) or None
    for key in ("commercial.emd", "commercial.bid_validity", "commercial.completion_period"):
        req = db.scalar(select(Requirement).where(Requirement.tender_id == tender.id, Requirement.key == key))
        v = current_version(req) if req else None
        if v:
            val = v.value
            if "amount" in val and try_D(val["amount"]) is not None:
                ctx[f"req.{key}"] = f"₹ {format_inr(val['amount'])}"
            elif "days" in val:
                ctx[f"req.{key}"] = f"{val['days']} days"
            elif "value" in val:
                ctx[f"req.{key}"] = f"{val['value']} {val.get('unit', '')}s"
    if quote is not None:
        ctx["quote.total"] = f"₹ {format_inr(quote.total)}"
        ctx["quote.total_words"] = amount_in_words(quote.total)
    return ctx


def requirement_keys_used() -> list[str]:
    return ["commercial.emd", "commercial.bid_validity", "commercial.completion_period", "deadline.bid_submission"]


def _val(ctx, path, missing: list[str]) -> str:
    v = ctx.get(path)
    if v in (None, ""):
        missing.append(path)
        return MISSING.format(FIELD_CATALOG.get(path, path))
    return str(v)


def _para(doc, text, bold=False, size=11, missing_marker=True):
    p = doc.add_paragraph()
    for part in re.split(r"(«MISSING: [^»]+»)", text):
        if not part:
            continue
        run = p.add_run(part)
        run.bold = bold
        run.font.size = Pt(size)
        if part.startswith("«MISSING") and missing_marker:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.bold = True
    return p


def render_builtin(db: Session, tender: Tender, key: str, ctx: dict, missing: list[str]) -> bytes:
    title, _env = BUILTINS[key]
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    v = lambda p: _val(ctx, p, missing)  # noqa: E731
    _para(doc, "DRAFT — generated by TenderPilot for review. Not signed.", bold=True, size=9)
    if key in ("cover_letter", "bid_form", "undertaking", "clarification_request", "financial_bid_letter"):
        _para(doc, f"Date: {v('letter.date')}")
        _para(doc, f"To,\n{v('tender.department')}")
        _para(doc, f"Subject: {title} — {v('tender.title')} (Tender ID: {v('tender.tender_ref')})", bold=True)
        _para(doc, "Dear Sir/Madam,")
    if key == "cover_letter":
        _para(doc, f"We, {v('entity.legal_name')}, having our registered office at {v('entity.registered_address')}, submit our "
                   f"bid for the above work in accordance with the tender documents, including all corrigenda issued up to the date of submission.")
        _para(doc, f"The bid security of {v('req.commercial.emd')} has been furnished as required. Our bid shall remain valid for "
                   f"{v('req.commercial.bid_validity')} from the date of opening.")
        _para(doc, "The enclosures are listed in the attached index. We confirm that the technical envelope contains no price information.")
    elif key == "bid_form":
        _para(doc, f"Having examined the tender documents, we offer to execute the work within {v('req.commercial.completion_period')} "
                   f"in conformity with the conditions of contract, specifications and drawings.")
        _para(doc, "We accept all terms and conditions of the tender without deviation, except as listed in the deviation schedule (if any).")
    elif key == "undertaking":
        _para(doc, f"We, {v('entity.legal_name')}, hereby declare that (a) we have not been blacklisted or debarred by any government "
                   "department or public sector undertaking; (b) all information and documents furnished are true and correct; and "
                   "(c) we shall be liable for rejection/forfeiture if any statement is found to be incorrect.")
    elif key == "experience_list":
        _para(doc, f"Schedule of similar works executed by {v('entity.legal_name')}", bold=True, size=13)
        ent_id = tender.bidder_entity_id
        recs = list(db.scalars(select(EvidenceRecord).where(EvidenceRecord.legal_entity_id == ent_id,
                                                            EvidenceRecord.category == "completion_certificate",
                                                            EvidenceRecord.verification_state == "verified",
                                                            EvidenceRecord.is_current.is_(True)))) if ent_id else []
        table = doc.add_table(rows=1, cols=6)
        table.style = "Table Grid"
        for i, h in enumerate(["S.No", "Name of work", "Employer", "Executed value (₹)", "Completion date", "Certificate ref"]):
            table.rows[0].cells[i].text = h
        if not recs:
            missing.append("experience.records")
            row = table.add_row().cells
            row[1].text = MISSING.format("verified completion certificates")
        for n, r in enumerate(recs, 1):
            f = r.fields
            row = table.add_row().cells
            row[0].text = str(n)
            row[1].text = f.get("work_name") or r.title
            row[2].text = r.issuer or f.get("employer") or MISSING.format("employer")
            row[3].text = format_inr(f["executed_value"]) if try_D(f.get("executed_value")) is not None else MISSING.format("value")
            row[4].text = f.get("completion_date") or MISSING.format("completion date")
            row[5].text = f"Vault #{r.id}"
    elif key == "poa_draft":
        _para(doc, "POWER OF ATTORNEY (DRAFT — to be executed on stamp paper of appropriate value and notarised as required)", bold=True)
        _para(doc, f"Know all men by these presents, we {v('entity.legal_name')} do hereby constitute and appoint "
                   f"{v('entity.signatory_name')}, {v('entity.signatory_designation')}, as our attorney to sign and submit the bid for "
                   f"{v('tender.title')} (Tender ID {v('tender.tender_ref')}) and to do all acts in connection therewith.")
        _para(doc, "This draft is not an executed instrument.", bold=True)
    elif key == "jv_agreement_draft":
        _para(doc, "JOINT VENTURE AGREEMENT (DRAFT — for legal review; not executed)", bold=True)
        _para(doc, f"JV name: {v('jv.name')}")
        _para(doc, f"Members and participation: {v('jv.members')}")
        _para(doc, f"Purpose: to bid for and, if awarded, execute {v('tender.title')} (Tender ID {v('tender.tender_ref')}). "
                   "The members shall be jointly and severally liable to the employer as required by the tender.")
    elif key == "clarification_request":
        _para(doc, "We request clarification on the following clauses (to be completed by the tender manager):")
        t = doc.add_table(rows=2, cols=4)
        t.style = "Table Grid"
        for i, h in enumerate(["S.No", "Clause / page", "Tender text", "Clarification sought"]):
            t.rows[0].cells[i].text = h
    elif key == "financial_bid_letter":
        _para(doc, f"We offer to execute the work for a total amount of {v('quote.total')} ({v('quote.total_words')}) "
                   "as per the priced bill of quantities submitted.")
    _para(doc, "")
    _para(doc, f"For {v('entity.legal_name')}")
    _para(doc, f"(Signature of authorised signatory — to be signed)\nName: {v('entity.signatory_name')}\n"
               f"Designation: {v('entity.signatory_designation')}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ uploaded templates
def _iter_paragraphs(doc):
    for p in doc.paragraphs:
        yield p
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for s in doc.sections:
        for p in list(s.header.paragraphs) + list(s.footer.paragraphs):
            yield p


def find_placeholders(data: bytes) -> list[str]:
    doc = Document(io.BytesIO(data))
    found: list[str] = []
    for p in _iter_paragraphs(doc):
        for m in PLACEHOLDER_RE.finditer(p.text):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found


def fill_template(data: bytes, mapping: dict[str, str | None], missing: list[str]) -> bytes:
    """Replace placeholders. Runs are rewritten only in paragraphs containing placeholders."""
    doc = Document(io.BytesIO(data))
    for p in _iter_paragraphs(doc):
        if "{{" not in p.text:
            continue
        text = p.text

        def sub(m):
            ph = m.group(1)
            val = mapping.get(ph)
            if val in (None, ""):
                missing.append(ph)
                return MISSING.format(ph)
            return str(val)
        new = PLACEHOLDER_RE.sub(sub, text)
        if p.runs:
            first = p.runs[0]
            first.text = new
            for r in p.runs[1:]:
                r.text = ""
            if "«MISSING" in new:
                first.font.highlight_color = WD_COLOR_INDEX.YELLOW
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ preview + QA
def docx_to_preview_pdf(docx_bytes: bytes, title: str, draft: bool = True) -> tuple[bytes, list[dict]]:
    """Render a faithful-enough review PDF (text + tables) with reportlab and run layout QA.

    This is a preview, not a conversion: for pixel-exact output install LibreOffice and use the
    conversion adapter (documented). QA flags empty cells, remaining markers and overlong cells.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    doc = Document(io.BytesIO(docx_bytes))
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    flow = []
    issues: list[dict] = []
    for block in doc.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            txt = "".join(n.text or "" for n in block.iter() if n.tag.endswith("}t"))
            if "«MISSING" in txt:
                issues.append({"type": "missing_field", "text": txt[:120]})
            html = escape(txt).replace("«", "<font backColor='#fff3a0'><b>«").replace("»", "»</b></font>")
            flow.append(Paragraph(html or "&nbsp;", body))
        elif tag == "tbl":
            from docx.table import Table as DocxTable
            t = DocxTable(block, doc)
            data = []
            for r_i, row in enumerate(t.rows):
                cells = []
                for c in row.cells:
                    ct = c.text
                    if not ct.strip() and r_i > 0:
                        issues.append({"type": "empty_cell", "row": r_i})
                    if len(ct) > 600:
                        issues.append({"type": "overflow_risk", "row": r_i, "chars": len(ct)})
                    if "«MISSING" in ct:
                        issues.append({"type": "missing_field", "text": ct[:120]})
                    cells.append(Paragraph(escape(ct), body))
                data.append(cells)
            if data:
                tbl = Table(data, repeatRows=1)
                tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                                         ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef3")),
                                         ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                flow.append(tbl)
                flow.append(Spacer(1, 4 * mm))

    def on_page(canvas, _doc):
        canvas.saveState()
        if draft:
            canvas.setFont("Helvetica-Bold", 60)
            canvas.setFillColor(colors.Color(0.85, 0.3, 0.3, alpha=0.15))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(15 * mm, 10 * mm, f"{title[:90]} — page {_doc.page}")

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=title).build(flow, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue(), issues


def pdf_signature_info(data: bytes) -> dict:
    """Detect embedded digital signatures (does not validate certificates — use a PKI validator for that)."""
    from pypdf import PdfReader
    try:
        r = PdfReader(io.BytesIO(data))
        root = r.trailer["/Root"]
        af = root.get("/AcroForm")
        sig_fields = 0
        if af is not None:
            af = af.get_object()
            for f in af.get("/Fields", []) or []:
                fo = f.get_object()
                if fo.get("/FT") == "/Sig" and fo.get("/V") is not None:
                    sig_fields += 1
        return {"pages": len(r.pages), "digital_signatures": sig_fields, "encrypted": r.is_encrypted}
    except Exception as exc:  # noqa: BLE001
        return {"pages": None, "digital_signatures": 0, "error": type(exc).__name__}
