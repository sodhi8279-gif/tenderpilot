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


# A second SYNTHETIC tender with technical qualification criteria and a JV sharing clause.
TECH_NIT_PAGES = [
    [
        "Demo City Water Supply Board (SYNTHETIC)",
        "NOTICE INVITING TENDER  NIT No. DCWSB/SYN/2026/044",
        "1. Name of work: Laying of 600 mm DI pumping main and construction of pump house at Demo City.",
        "2. Estimated cost put to tender: Rs. 24.00 Crore",
        "3. Earnest Money Deposit (EMD): Rs. 24,00,000 by online payment.",
        "4. Period of completion: 24 months.",
        "5. Last date and time for submission of bids: {deadline} at 15:00 hrs IST",
        "6. Date of opening of technical bids: {opening} at 15:30 hrs IST",
        "7. The tender follows a two bid system (technical and financial envelopes).",
    ],
    [
        "8. QUALIFICATION CRITERIA",
        "8.1 Average annual financial turnover during the last 3 financial years should be at least",
        "Rs. 12 Crore, as certified by a Chartered Accountant.",
        "8.2 The bidder should have satisfactorily completed during the last seven years two similar",
        "completed works each costing not less than 50% of the estimated cost. Similar work means laying",
        "of DI or MS pumping main of 300 mm dia and above.",
        "8.3 Net worth of the bidder should be positive.",
        "8.4 The bidder should be registered with CPWD in Class I or above.",
        "8.5 Physical performance: The bidder should have executed in any one financial year",
        "during the last five years the following minimum quantities:",
        "(a) Laying of DI pipes of 300 mm dia and above: 15 km",
        "(b) Earthwork in excavation: 50,000 cum",
        "(c) RCC: 3,000 cum",
        "8.6 Key personnel: The bidder shall deploy the following key personnel:",
        "(i) Project Manager - B.E./B.Tech (Civil) with 12 years experience - 1 No.",
        "(ii) Site Engineer - B.E. (Civil) or Diploma (Civil) with 5 years experience - 2 Nos.",
        "(iii) Mechanical Engineer - B.E. (Mechanical) with 5 years experience - 1 No.",
        "8.7 Key plant and machinery: The bidder should own or have on lease the following:",
        "(i) Hydraulic excavator - 2 Nos.",
        "(ii) Crane of capacity 10 MT - 1 No.",
        "(iii) Hydrostatic test pump - 1 No.",
        "(iv) DG set 125 kVA - 1 No.",
        "8.8 Qualification shall be assessed as on the last date of submission of bids.",
    ],
    [
        "9. JOINT VENTURE",
        "9.1 Joint venture is permitted with not more than two members.",
        "9.2 The lead member shall have a share of not less than 51% in the joint venture.",
        "9.3 The JV members shall jointly meet the qualification criteria. The lead member shall meet",
        "at least 60% of the financial and physical qualification criteria and each other member shall",
        "meet at least 30%. The similar work criterion shall be met by the lead member alone.",
        "9.4 Each member shall have positive net worth.",
        "10. DOCUMENTS TO BE UPLOADED",
        "10.1 Copy of PAN card of the bidder.",
        "10.2 Power of attorney in favour of the person signing the bid.",
    ],
]


# SYNTHETIC records for two fictional companies that qualify for TECH_NIT only when they bid together:
# Alpha has the finance, similar works and most quantities; Beta has the mechanical staff and equipment.
TECH_PARTNERS = {
    "alpha": {"legal_name": "Alpha Pipelines Private Limited (SYNTHETIC)", "records": [
        ("turnover_certificate", "CA turnover FY 2025-26", {"fy": "2025-26", "turnover": "100000000"}, None),
        ("turnover_certificate", "CA turnover FY 2024-25", {"fy": "2024-25", "turnover": "100000000"}, None),
        ("turnover_certificate", "CA turnover FY 2023-24", {"fy": "2023-24", "turnover": "100000000"}, None),
        ("completion_certificate", "Completion: 500 mm DI main, Demo North", {"work_name": "500 mm DI main, Demo North",
         "executed_value": "130000000", "completion_date": "2024-06-30", "status": "completed", "is_similar": True}, None),
        ("completion_certificate", "Completion: 400 mm MS main, Demo East", {"work_name": "400 mm MS main, Demo East",
         "executed_value": "125000000", "completion_date": "2023-02-15", "status": "completed", "is_similar": True}, None),
        ("net_worth", "CA net worth certificate", {"net_worth": "60000000", "as_of": "2026-03-31"}, None),
        ("registration", "CPWD registration Class I", {"authority": "CPWD", "class": "I"}, "2028-03-31"),
        ("pan", "PAN card", {}, None),
        ("power_of_attorney", "Power of attorney", {}, None),
        ("executed_quantity", "DI pipe laid FY 2024-25", {"work_name": "500 mm DI main, Demo North", "item": "Laying DI pipes",
         "quantity": "10", "unit": "km", "fy": "2024-25", "dia_mm": "500", "material": "DI"}, None),
        ("executed_quantity", "Earthwork FY 2024-25", {"work_name": "500 mm DI main, Demo North", "item": "Earthwork in excavation",
         "quantity": "40000", "unit": "cum", "fy": "2024-25"}, None),
        ("executed_quantity", "RCC FY 2024-25", {"work_name": "Pump house, Demo North", "item": "RCC", "quantity": "2000",
         "unit": "cum", "fy": "2024-25"}, None),
        ("personnel", "CV: R. Menon (SYNTHETIC)", {"person_name": "R. Menon (SYNTHETIC)", "qualification": "B.E. Civil",
         "experience_years": "15"}, None),
        ("personnel", "CV: S. Das (SYNTHETIC)", {"person_name": "S. Das (SYNTHETIC)", "qualification": "Diploma in Civil Engineering",
         "experience_years": "6"}, None),
        ("machinery", "Excavator invoice", {"equipment_type": "Hydraulic excavator", "quantity": "1", "ownership": "owned"}, None),
        ("machinery", "Crane RC book", {"equipment_type": "Hydra crane", "quantity": "1", "capacity": "12", "capacity_unit": "MT",
         "ownership": "owned"}, None),
    ]},
    "beta": {"legal_name": "Beta Mech Works LLP (SYNTHETIC)", "records": [
        ("turnover_certificate", "CA turnover FY 2025-26", {"fy": "2025-26", "turnover": "40000000"}, None),
        ("turnover_certificate", "CA turnover FY 2024-25", {"fy": "2024-25", "turnover": "40000000"}, None),
        ("turnover_certificate", "CA turnover FY 2023-24", {"fy": "2023-24", "turnover": "40000000"}, None),
        ("net_worth", "CA net worth certificate", {"net_worth": "15000000", "as_of": "2026-03-31"}, None),
        ("registration", "CPWD registration Class I", {"authority": "CPWD", "class": "I"}, "2027-12-31"),
        ("pan", "PAN card", {}, None),
        ("power_of_attorney", "Power of attorney", {}, None),
        ("executed_quantity", "DI pipe laid FY 2024-25", {"work_name": "300 mm DI rising main", "item": "DI pipe laying",
         "quantity": "6", "unit": "km", "fy": "2024-25", "dia_mm": "300", "material": "DI"}, None),
        ("executed_quantity", "Earthwork FY 2024-25", {"work_name": "300 mm DI rising main", "item": "Earth work excavation",
         "quantity": "15000", "unit": "cum", "fy": "2024-25"}, None),
        ("executed_quantity", "RCC FY 2024-25", {"work_name": "Sump, Demo West", "item": "R.C.C. work", "quantity": "1200",
         "unit": "cum", "fy": "2024-25"}, None),
        ("personnel", "CV: K. Iyer (SYNTHETIC)", {"person_name": "K. Iyer (SYNTHETIC)", "qualification": "B.Tech Civil",
         "experience_years": "8"}, None),
        ("personnel", "CV: M. Khan (SYNTHETIC)", {"person_name": "M. Khan (SYNTHETIC)", "qualification": "B.E. Mechanical",
         "experience_years": "7"}, None),
        ("machinery", "Excavator invoice", {"equipment_type": "Excavator (Poclain)", "quantity": "1", "ownership": "owned"}, None),
        ("machinery", "Hydro test pump invoice", {"equipment_type": "Hydrostatic test pump", "quantity": "1", "ownership": "owned"}, None),
        ("machinery", "DG set hire agreement", {"equipment_type": "DG set", "quantity": "1", "capacity": "125", "capacity_unit": "kVA",
         "ownership": "leased"}, "2027-06-30"),
    ]},
}


def technical_nit_pdf() -> bytes:
    """3 text pages: financial, similar work, registration class, physical quantities, personnel, machinery, JV split."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, page in enumerate(TECH_NIT_PAGES):
        _text_page(c, _fmt(page), f"Page {i + 1}")
    c.save()
    return buf.getvalue()


# Layout of a typical state-PSU slope-protection NIT: estimate and class in a table whose cells wrap, criteria under
# "TECHNICAL CRITERIA" with A) General / B) Specific experience / C) Plant, Machinery & Equipment, bullets printed on
# their own line, sizes wrapped onto the next line, the JV notes split over pages, and manpower in running text.
EXP_NIT_PAGES = [
    [
        "Hill Power Development Corporation, Demo Valley (SYNTHETIC)",
        "e-NIT No. HPDC/SYN/2026/031",
        # table cells come out of the PDF one cell (and one wrapped cell line) at a time
        "Description of", "Work", "Estimated", "cost of", "work", "Cost of Bid", "Document", "(INR)",
        "Comple", "tion", "Period", "(Mont", "hs)", "Class", "of", "Contr", "actor",
        "Repair of slopes", "near adit portal", "(SYNTHETIC)", "6.7956", "Crore", "5000/-", "06", "month",
        "Class", "A and", "above",
        "Last date and time for submission of bids: {deadline} at 15:00 hrs IST",
    ],
    [
        "1.5. The Applicant shall be a single entity / sole bidder or a Joint venture duly constituted with",
        "maximum 2 partners.",
        "1.6. TECHNICAL CRITERIA:",
        "A) General Experience",
        "The bidder/applicant, as a Sole Contractor or as a member of a Joint Venture, should have",
        "experience of executing construction of retaining wall of height not less than 7.5 m and length not",
        "less than 10 m during the preceding ten (10) years (reckoned from one month prior to the date of",
        "issue of NIT), of value as under:",
        "•",
        "One completed or ongoing project having executed value not less than 80% of the advertised",
        "cost of work",
        "or",
        "•",
        "Two completed or ongoing projects each having executed value not less than 50% of the",
        "advertised cost of work",
        "or",
        "•",
        "Three completed or ongoing projects each having executed value not less than 40% of the",
        "advertised cost of work",
    ],
    [
        "The experience claim should be substantiated by completion certificate/ Allotment Order.",
        "In case of Joint Venture (JV):",
        "•",
        "Experience of all JV partners shall be aggregated to assess compliance with the above criteria.",
        "B) Specific Experience",
        "The bidder, either individually or through participation in a Joint Venture, must demonstrate the",
        "following successful experience within the preceding ten (10) years:",
        "•",
        "Construction of RCC/PCC slope protection works with anchor bolts of height not less than",
        "5 m and length not less than 20 m.",
        "In case of Joint Venture (JV):",
        "•",
        "The specific experience may be furnished by any one partner,",
        "C) Plant, Machinery & Equipment",
        "The bidder (individual or JV) must own or have assured access (through lease/loan agreements) to the",
        "following key equipment:",
        "i. Concrete Batching Plant of required capacity",
        "ii. Transit Mixers and Concrete Pumps capable of placing concrete in confined spaces",
        "iii. Drilling Rigs (minimum 45 mm dia.) for anchoring works",
        "iv. Epoxy Injection / Grouting Equipment",
        "In case of Joint Venture (JV):",
        "•",
        "The required equipment may be owned or arranged collectively by the JV partners.",
    ],
    [
        "8. Affidavit Regarding Manpower: The bidder shall submit an affidavit confirming the availability of",
        "adequate qualified technical staff, skilled workers and other manpower required for execution of",
        "the work. The bidder should confirm and share details regarding availabity of atleast 01 no. Civil",
        "Engineer with minimum 05 years of work experience, 01 no. Safety Officer, on its regular payroll.",
        "2.2. II. The Lead Partner shall hold a minimum stake of 51% in the Joint Venture (JV) and shall",
        "individually meet at least 50% of the Technical Qualification Criteria specified under Clause 1.6(B)",
        "4. Foreign bidders, joint ventures with foreign partners, or consortiums involving foreign entities",
        "are not permitted.",
    ],
]


def experience_nit_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for i, page in enumerate(EXP_NIT_PAGES):
        _text_page(c, _fmt(page), f"Page {i + 1}")
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
