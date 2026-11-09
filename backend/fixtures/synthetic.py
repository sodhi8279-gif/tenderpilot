"""SYNTHETIC demonstration fixtures. Every organization, tender and figure here is fictional.

Generated in memory so no real tender or company document is ever committed.
"""
from __future__ import annotations

import io
from datetime import date, timedelta

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

SYNTHETIC_BANNER = "SYNTHETIC FIXTURE - NOT A REAL TENDER"

# Dates are relative to today so the fixtures never expire.
DEADLINE = date.today() + timedelta(days=16)
OPENING = DEADLINE + timedelta(days=1)
EXTENDED = DEADLINE + timedelta(days=10)


def _d(x: date) -> str:
    return x.strftime("%d/%m/%Y")


def _fmt(lines: list[str]) -> list[str]:
    return [ln.format(deadline=_d(DEADLINE), opening=_d(OPENING), extended=_d(EXTENDED)) for ln in lines]

NIT_PAGES = [
    [
        "Department of Water Works, Demo District (SYNTHETIC)",
        "NOTICE INVITING TENDER  NIT No. DWW/SYN/2026/017",
        "1. Name of work: Construction of 10 MLD Water Treatment Plant with intake, pumping main and",
        "   distribution network at Demo Town including 5 years O&M.",
        "2. Estimated cost put to tender: Rs. 12.50 Crore",
        "3. Earnest Money Deposit (EMD): Rs. 12,50,000 in the form of online payment or bank guarantee.",
        "4. Tender document fee: Rs. 5,000 (non-refundable).",
        "5. Period of completion: 18 months including monsoon.",
        "6. Last date and time for submission of bids: {deadline} at 15:00 hrs IST",
        "7. Date of opening of technical bids: {opening} at 15:30 hrs IST",
        "8. Bids shall remain valid for 90 days from the date of opening of technical bids.",
        "9. The tender follows a two bid system (technical and financial envelopes).",
    ],
    [
        "10. QUALIFICATION CRITERIA",
        "10.1 Average annual financial turnover during the last 3 financial years should be at least",
        "Rs. 7.50 Crore, as certified by a Chartered Accountant.",
        "10.2 The bidder should have satisfactorily completed during the last seven years ending the last",
        "day of the month previous to the one in which bids are invited: three similar completed works",
        "each costing not less than 40% of the estimated cost; or two similar completed works each costing",
        "not less than 60% of the estimated cost; or one similar completed work costing not less than 80%",
        "of the estimated cost. Similar work means construction of water treatment plant.",
        "10.3 Net worth of the bidder should be positive as per the latest audited balance sheet.",
        "10.4 Solvency certificate of Rs. 5 Crore from a scheduled bank.",
        "10.5 Qualification shall be assessed as on the last date of submission of bids.",
    ],
    [
        "11. JOINT VENTURE",
        "11.1 Joint venture is permitted with not more than two members.",
        "11.2 The lead member shall have a share of not less than 51% in the joint venture.",
        "12. DOCUMENTS TO BE UPLOADED",
        "12.1 Copy of PAN card of the bidder.",
        "12.2 GST registration certificate.",
        "12.3 ISO 9001 certificate valid on the last date of submission.",
        "12.4 Power of attorney in favour of the person signing the bid.",
        "13. Performance security: 5% of the contract value in the form of a bank guarantee.",
        "14. Liquidated damages: 0.5% of contract value per week of delay, subject to a maximum of 10% of contract value.",
        "15. Defect liability period: 12 months from completion.",
    ],
]

CORRIGENDUM_LINES = [
    "Department of Water Works, Demo District (SYNTHETIC)",
    "CORRIGENDUM No. 1 to NIT No. DWW/SYN/2026/017",
    "1. The last date for submission of bids is extended to {extended} at 15:00 hrs IST.",
    "2. All other terms and conditions of the tender remain unchanged.",
]


def _text_page(c: canvas.Canvas, lines: list[str], label: str | None = None) -> None:
    c.setFont("Helvetica-Bold", 7)
    c.drawString(40, 815, SYNTHETIC_BANNER)
    c.setFont("Helvetica", 9.5)
    y = 780
    for ln in lines:
        c.drawString(40, y, ln)
        y -= 16
    if label:
        c.setFont("Helvetica", 8)
        c.drawString(290, 30, label)
    c.showPage()


def _scanned_page(c: canvas.Canvas, lines: list[str]) -> None:
    """An image-only page (simulates a scanned annexure): no text layer at all."""
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    y = 120
    for ln in lines:
        d.text((90, y), ln, fill="black")
        y += 40
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, width=A4[0], height=A4[1])
    c.showPage()


def nit_pdf() -> bytes:
    """5 pages: 3 text pages, 1 scanned (image-only) page, 1 duplicate of page 3."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, page in enumerate(NIT_PAGES):
        _text_page(c, _fmt(page), f"Page {i + 1}")
    _scanned_page(c, ["ANNEXURE-S (scanned)", "Site location sketch - synthetic", "Signed by Executive Engineer"])
    _text_page(c, _fmt(NIT_PAGES[2]), "Page 3")  # duplicate page
    c.save()
    return buf.getvalue()


def corrigendum_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _text_page(c, _fmt(CORRIGENDUM_LINES), "Page 1")
    c.save()
    return buf.getvalue()


def simple_pdf(title: str, lines: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _text_page(c, [title, "SYNTHETIC evidence document for testing."] + (lines or []))
    c.save()
    return buf.getvalue()


BOQ_ITEMS = [
    ("1", "Earth work in excavation in all kinds of soil for foundation", "cum", 1200),
    ("2", "Providing and laying M25 grade reinforced cement concrete", "cum", 850.5),
    ("3", "Supply, laying and jointing DI K9 pipes 300 mm dia", "rmt", 4200),
    ("4", "Supply, installation of vertical turbine pump set 75 kW", "nos", 3),
    ("5", "Supply and laying of 3.5 core 240 sq mm XLPE cable", "rmt", 640),
]


def boq_xlsx(protect: bool = True) -> bytes:
    import openpyxl
    from openpyxl.styles import Font, Protection
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOQ"
    ws["A1"] = "Name of work: 10 MLD WTP at Demo Town (SYNTHETIC)"
    ws["A1"].font = Font(bold=True)
    ws.merge_cells("A1:F1")
    headers = ["Item No", "Description", "Quantity", "Unit", "Rate (Rs)", "Amount (Rs)"]
    for i, h in enumerate(headers, 1):
        ws.cell(3, i, h).font = Font(bold=True)
    r = 4
    for code, desc, unit, qty in BOQ_ITEMS:
        ws.cell(r, 1, code)
        ws.cell(r, 2, desc)
        ws.cell(r, 3, qty)
        ws.cell(r, 4, unit)
        ws.cell(r, 5).protection = Protection(locked=False)
        ws.cell(r, 6, f"=C{r}*E{r}")
        r += 1
    ws.cell(r, 2, "TOTAL")
    ws.cell(r, 6, f"=SUM(F4:F{r - 1})")
    notes = wb.create_sheet("Instructions")
    notes["A1"] = "Bidders shall quote rates in column E only. Do not alter any other cell."
    hidden = wb.create_sheet("DeptRef")
    hidden["A1"] = "Department reference data (hidden)"
    hidden.sheet_state = "hidden"
    if protect:
        ws.protection.sheet = True
        ws.protection.password = "synthetic"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
