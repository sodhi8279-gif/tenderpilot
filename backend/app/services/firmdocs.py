"""Read a client firm's document and suggest what to record: the kind of document, its dates and values, and the
firm details printed on it (PAN, GSTIN, CIN, Udyam, address, bank account ...).

* Offline only: the text layer, or OCR (Tesseract) for scans and photos. Nothing is sent outside this computer.
* Nothing is saved here. The person sees every suggestion next to the line it came from and decides; records saved
  from suggestions stay unverified until a reviewer checks them against the original, like any other record.
* Firm details never overwrite what is already recorded unless the person ticks that line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .dates import DATE_RE, parse_date
from .ingest import (OcrAdapter, docx_text_pages, get_ocr, open_pdf, process_image, process_pdf_page, sniff_kind, UnsafeFile,
                     xlsx_text_pages)
from .money import parse_indian_amount

READER_VERSION = "firmdoc-1"
MAX_PAGES = 8  # certificates are short; long files (audited accounts) carry what we need on the first pages

TITLES = {
    "gst": "GST registration certificate", "pan": "PAN card", "incorporation": "Certificate of incorporation",
    "registration": "Registration certificate", "turnover_certificate": "Turnover certificate (CA)",
    "audited_accounts": "Audited accounts", "net_worth": "Net worth certificate", "solvency": "Solvency certificate",
    "completion_certificate": "Completion certificate", "performance_certificate": "Performance certificate",
    "work_order": "Work order", "agreement": "Contract agreement", "quality_certification": "Quality certificate",
    "power_of_attorney": "Power of attorney", "machinery": "Machinery document", "personnel": "Personnel CV",
    "bank_facility": "Bank document", "other": "Document",
}

# (category, weight, pattern). The category with the highest total wins; below 3 it stays "other".
SIGNS: list[tuple[str, int, str]] = [
    ("gst", 5, r"REG\s*-?\s*06"), ("gst", 2, r"Goods\s+and\s+Services\s+Tax"), ("gst", 3, r"Principal\s+Place\s+of\s+Business"),
    ("gst", 1, r"Registration\s+Certificate"),
    ("pan", 5, r"Permanent\s+Account\s+Number"), ("pan", 3, r"INCOME\s*TAX\s+DEPARTMENT"),
    ("incorporation", 5, r"Certificate\s+of\s+Incorporation"), ("incorporation", 3, r"Registrar\s+of\s+Companies"),
    ("incorporation", 2, r"Corporate\s+Identity\s+Number"),
    ("registration", 5, r"UDYAM\s+REGISTRATION"), ("registration", 2, r"\bUDYAM-"),
    ("registration", 4, r"(?:registration|enlistment|enrolment)\s+(?:of|as)\s+(?:a\s+)?contractor"),
    ("registration", 3, r"Employees'?\s+Provident\s+Fund"), ("registration", 3, r"Employees'?\s+State\s+Insurance"),
    ("registration", 2, r"\bClass\s*[-:]?\s*(?:[IVX]{1,4}|[A-E])\b.{0,30}contractor"),
    ("turnover_certificate", 3, r"\bturn\s*-?\s*over\b"), ("turnover_certificate", 2, r"Chartered\s+Accountants?"),
    ("turnover_certificate", 1, r"\bUDIN\b"),
    ("audited_accounts", 4, r"Balance\s+Sheet\s+as\s+at"), ("audited_accounts", 3, r"Statement\s+of\s+Profit\s+and\s+Loss|Profit\s*(?:&|and)\s*Loss\s+Account"),
    ("net_worth", 4, r"\bnet\s*-?\s*worth\b"),
    ("solvency", 6, r"\bsolvency\b"),
    ("completion_certificate", 6, r"completion\s+certificate"), ("completion_certificate", 2, r"(?:has|have)\s+(?:been\s+)?(?:successfully\s+)?completed"),
    ("completion_certificate", 2, r"value\s+of\s+work\s+(?:done|executed|completed)"),
    ("performance_certificate", 7, r"performance\s+certificate"),
    ("work_order", 5, r"\bwork\s+order\b"), ("work_order", 4, r"letter\s+of\s+(?:acceptance|award|intent)"),
    ("work_order", 2, r"you\s+are\s+(?:hereby\s+)?(?:requested|directed)\s+to\s+(?:commence|start)"),
    ("agreement", 4, r"(?:contract\s+)?agreement\s+(?:no|number)"), ("agreement", 2, r"this\s+agreement\s+is\s+made"),
    ("quality_certification", 6, r"\bISO\s*(?:9001|14001|45001|27001)"),
    ("power_of_attorney", 7, r"power\s+of\s+attorney"),
    ("machinery", 4, r"chassis\s+no|engine\s+no"), ("machinery", 2, r"registration\s+certificate\s+of\s+(?:a\s+)?(?:motor\s+)?vehicle"),
    ("personnel", 5, r"curriculum\s+vitae|\bresume\b|bio-?\s*data"),
    ("bank_facility", 4, r"cancell?ed\s+cheque|bank\s+guarantee|credit\s+facility|sanction\s+letter"),
]

PAN = r"[A-Z]{5}[0-9]{4}[A-Z]"
GSTIN = r"[0-3][0-9][A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
CONSTITUTION = [("private limited", "company"), ("public limited", "company"), ("limited company", "company"),
                ("one person company", "company"), ("limited liability partnership", "llp"), ("llp", "llp"),
                ("partnership", "partnership"), ("proprietorship", "proprietorship"), ("proprietor", "proprietorship"),
                ("society", "society"), ("trust", "society"), ("public sector", "public_sector"),
                ("government", "public_sector")]
FIRM_LABELS = {
    "legal_name": "Legal name", "entity_type": "Constitution", "registered_address": "Registered address", "pan": "PAN",
    "gstin": "GSTIN", "cin": "CIN / LLPIN / registration no.", "incorporation_date": "Date of incorporation", "tan": "TAN",
    "udyam": "Udyam (MSME) registration no.", "msme_category": "MSME category", "epf": "EPF code", "esi": "ESI code",
    "email": "E-mail", "phone": "Phone", "website": "Website", "bank_name": "Bank (EMD refunds)",
    "bank_account": "Account no.", "bank_ifsc": "IFSC",
}
CORE = {"legal_name", "entity_type", "registered_address", "pan", "gstin"}  # LegalEntity columns; the rest is profile


@dataclass
class Reading:
    filename: str
    kind: str
    pages_total: int = 0
    pages_read: int = 0
    read_by: str = ""
    notes: list[str] = field(default_factory=list)
    text: str = ""


def read_text(data: bytes, filename: str, ocr: OcrAdapter | None = None) -> Reading:
    """All the text TenderPilot can get from the file (first MAX_PAGES pages)."""
    try:
        kind = sniff_kind(data, filename)
    except UnsafeFile as exc:
        return Reading(filename, "unknown", notes=[f"{exc}. Enter the values yourself."])
    r = Reading(filename, kind)
    sources: set[str] = set()
    pages = []
    if kind == "pdf":
        ocr = ocr or get_ocr()
        doc = open_pdf(data)
        try:
            r.pages_total = doc.page_count
            for i in range(min(doc.page_count, MAX_PAGES)):
                pages.append(process_pdf_page(doc, i, ocr))
        finally:
            doc.close()
        if r.pages_total > MAX_PAGES:
            r.notes.append(f"Read the first {MAX_PAGES} of {r.pages_total} pages.")
    elif kind == "image":
        pages = [process_image(data, ocr or get_ocr())]
        r.pages_total = 1
    elif kind == "docx":
        pages = docx_text_pages(data)
        r.pages_total = 1
    elif kind == "xlsx":
        pages = xlsx_text_pages(data)
        r.pages_total = len(pages)
    else:
        r.notes.append("This kind of file cannot be read; enter the values yourself.")
        return r
    texts = []
    for p in pages:
        if p.text and p.status == "processed":
            texts.append(p.text)
            sources.add(p.text_source or "")
            r.pages_read += 1
        elif p.reason:
            r.notes.append(f"Page {p.index + 1}: {p.reason}")
    r.text = "\n".join(texts)
    r.read_by = "scanned (OCR)" if sources == {"ocr"} else "scanned and text" if "ocr" in sources else "text" if sources else ""
    if "ocr" in sources:
        r.notes.append("Read from a scan: check every value against the document.")
    return r


# ------------------------------------------------------------------- helpers
def _flat(text: str) -> str:
    return re.sub(r"[ \t ]+", " ", text)


def _excerpt(text: str, start: int, end: int) -> str:
    a, b = max(0, start - 40), min(len(text), end + 40)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    out = " ".join(text[a:b].split())
    return out if len(out) <= 180 else out[:177].rsplit(" ", 1)[0] + "…"


def _after(text: str, label: str, max_len: int = 200, lines: int = 1) -> tuple[str, str] | None:
    """Value printed after a label ("Legal Name : X"), on the same line or the next ones."""
    m = re.search(rf"(?:{label})\s*[:\-–.]*\s*", text, re.I)
    if not m:
        return None
    rest = text[m.end():m.end() + max_len * 2]
    got = []
    for ln in rest.split("\n"):
        ln = ln.strip(" :-–|\t")
        if not ln:
            if got:
                break
            continue
        if got and re.match(r"^\s*(?:\d{1,2}[.)]\s|[A-Z][A-Za-z /&()'.]{2,40}\s*:)", ln):
            break  # next labelled line
        got.append(ln)
        if len(got) >= lines:
            break
    val = " ".join(got).strip(" ,.;:")[:max_len]
    return (val, _excerpt(text, m.start(), m.end() + len(val))) if val else None


def _date_after(text: str, label: str) -> tuple[str, str] | None:
    m = re.search(rf"(?:{label})[^\n\d]{{0,40}}", text, re.I)
    if not m:
        return None
    window = text[m.end():m.end() + 60]
    dm = DATE_RE.search(window)
    if not dm or dm.start() > 25:  # the date must follow the label, not sit somewhere further on
        return None
    pd = parse_date(window[dm.start():])
    if pd.value and 1950 <= pd.value.year <= 2100:
        return pd.value.isoformat(), _excerpt(text, m.start(), m.end() + 30)
    return None


def _first_date(text: str) -> str | None:
    pd = parse_date(text[:3000])
    return pd.value.isoformat() if pd.value and 1950 <= pd.value.year <= 2100 else None


def _amount_after(text: str, label: str) -> tuple[str, str] | None:
    m = re.search(rf"(?:{label})[^\d\n]{{0,60}}?((?:Rs\.?|₹|INR)?\s*\d[\d,]*(?:\.\d+)?\s*(?:crores?|cr\.?|lakhs?|lacs?)?)", text, re.I)
    if not m:
        return None
    amt = parse_indian_amount(m.group(1))
    if amt is None or amt <= 0:
        return None
    return _money(amt), _excerpt(text, m.start(), m.end())


def _money(v: Decimal) -> str:
    v = v.quantize(Decimal("0.01"))
    return str(v.to_integral()) if v == v.to_integral() else str(v)


def _clean_name(s: str) -> str:
    s = re.split(r"\s{2,}|\s\((?:PAN|GST|CIN|Regd)|\s(?:having|bearing|with|vide|for|at|is|has|who|as per|as under|of address)\s",
                 s, maxsplit=1)[0]
    return s.strip(" ,.;:-")[:200]


def classify(text: str) -> tuple[str, int]:
    scores: dict[str, int] = {}
    for cat, w, pat in SIGNS:
        if re.search(pat, text, re.I):
            scores[cat] = scores.get(cat, 0) + w
    if not scores:
        return "other", 0
    cat, score = max(scores.items(), key=lambda kv: kv[1])
    return (cat, score) if score >= 3 else ("other", score)


# ------------------------------------------------------------------- identifiers read from scans
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "T": "7"})
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A"})


def gstin_check_ok(g: str) -> bool:
    """The 15th character of a GSTIN is a check digit over the first 14 (GSTN's published method)."""
    s = 0
    for i, ch in enumerate(g[:14]):
        p = _B36.index(ch) * (2 if i % 2 else 1)
        s += p // 36 + p % 36
    return _B36[(36 - s % 36) % 36] == g[14]


def _repair(token: str, mask: str) -> str:
    """Put digits where the format needs digits and letters where it needs letters (OCR reads 0/O, 1/I/L ...)."""
    return "".join(ch.translate(_TO_DIGIT) if m == "9" else ch.translate(_TO_LETTER) if m == "A" else ch
                   for ch, m in zip(token, mask))


def find_gstin(t: str) -> tuple[str, int, int, str | None] | None:
    """(gstin, start, end, note). Exact first; otherwise a scan misreading repaired only if the check digit agrees."""
    m = re.search(rf"(?<![A-Z0-9]){GSTIN}(?![A-Z0-9])", t)
    if m:
        note = None if gstin_check_ok(m.group(0)) else "The GSTIN's check digit does not match: check it against the document."
        return m.group(0), m.start(), m.end(), note
    for m in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]{15}(?![A-Za-z0-9])", t):
        raw = m.group(0).upper()
        fixed = _repair(raw, "99AAAAA9999AXZX")
        if re.fullmatch(GSTIN, fixed) and gstin_check_ok(fixed):
            return fixed, m.start(), m.end(), f"Read as '{raw}' on the scan; corrected to a valid GSTIN."
    return None


# ------------------------------------------------------------------- firm details
def firm_details(text: str, category: str) -> dict[str, dict]:
    t = _flat(text)
    out: dict[str, dict] = {}

    def put(key, value, excerpt):
        if value and key not in out:
            out[key] = {"value": value, "label": FIRM_LABELS[key], "target": "core" if key in CORE else "profile",
                        "excerpt": excerpt}

    g = find_gstin(t)
    if g:
        put("gstin", g[0], _excerpt(t, g[1], g[2]))
        if g[3]:
            out["gstin"]["note"] = g[3]
        put("pan", g[0][2:12], f"Part of GSTIN {g[0]}")
    for m in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z0-9]{10}(?![A-Za-z0-9])", t):
        near = t[max(0, m.start() - 80):m.start()]
        if not (category == "pan" or re.search(r"\bPAN\b|Permanent\s+Account", near, re.I)):
            continue
        raw = m.group(0).upper()
        fixed = raw if re.fullmatch(PAN, raw) else _repair(raw, "AAAAA9999A")
        if re.fullmatch(PAN, fixed) and (fixed == raw or re.search(r"\d", raw)):
            put("pan", fixed, _excerpt(t, m.start(), m.end()))
            if fixed != raw:
                out["pan"]["note"] = f"Read as '{raw}' on the scan; corrected to the PAN format."
            break
    m = re.search(r"(?<![A-Z0-9])[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}(?![A-Z0-9])", t)
    if m:
        put("cin", m.group(0), _excerpt(t, m.start(), m.end()))
    else:
        m = re.search(r"LLPIN[^A-Z0-9]{0,20}([A-Z]{3}-\d{4})", t)
        if m:
            put("cin", m.group(1), _excerpt(t, m.start(), m.end()))
    m = re.search(r"UDYAM-[A-Z]{2}-\d{2}-\d{7}", t, re.I)
    if m:
        put("udyam", m.group(0).upper(), _excerpt(t, m.start(), m.end()))
        c = re.search(r"(?:Type\s+of\s+Enterprise|Enterprise\s+Type|Category)[^\n]{0,60}?\b(Micro|Small|Medium)\b", t, re.I)
        if c:
            put("msme_category", c.group(1).capitalize(), _excerpt(t, c.start(), c.end()))
    m = re.search(r"\bTAN\b[^A-Z0-9]{0,25}([A-Z]{4}\d{5}[A-Z])\b", t)
    if m:
        put("tan", m.group(1), _excerpt(t, m.start(), m.end()))
    m = re.search(r"(?:EPF|PF|Provident\s+Fund|Establishment)\s*(?:Code|Id|No\.?|Number)?[^A-Z0-9\n]{0,25}([A-Z]{5}\d{7,10})\b", t, re.I)
    if m:
        put("epf", m.group(1).upper(), _excerpt(t, m.start(), m.end()))
    m = re.search(r"(?:ESI|ESIC|Employer\s+Code)[^\d\n]{0,30}(\d{17})\b", t, re.I)
    if m:
        put("esi", m.group(1), _excerpt(t, m.start(), m.end()))
    m = re.search(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", t)
    if m and re.search(r"IFS", t[max(0, m.start() - 30):m.start()], re.I):
        put("bank_ifsc", m.group(1), _excerpt(t, m.start(), m.end()))
    m = re.search(r"(?:A/?C|Account)\s*(?:No|Number)\.?\s*[:\-]?\s*(\d[\d ]{7,20}\d)", t, re.I)
    if m:
        put("bank_account", m.group(1).replace(" ", ""), _excerpt(t, m.start(), m.end()))
    m = re.search(r"\b(State Bank of India|Punjab National Bank|Bank of Baroda|Canara Bank|Union Bank of India|"
                  r"Bank of India|Indian Bank|Central Bank of India|Indian Overseas Bank|UCO Bank|Bank of Maharashtra|"
                  r"Punjab & Sind Bank|HDFC Bank|ICICI Bank|Axis Bank|Kotak Mahindra Bank|IndusInd Bank|Yes Bank|"
                  r"IDFC First Bank|Federal Bank|J ?& ?K Bank|Jammu (?:and|&) Kashmir Bank)\b", t, re.I)
    if m and ("bank_ifsc" in out or "bank_account" in out):
        put("bank_name", m.group(1), _excerpt(t, m.start(), m.end()))
    if category not in ("work_order", "completion_certificate", "performance_certificate", "agreement"):
        # e-mail / phone / website on a work order are usually the department's, not the firm's
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", t)
        if m:
            put("email", m.group(0).strip(".").lower(), _excerpt(t, m.start(), m.end()))
        m = re.search(r"(?<!\d)(?:\+91[\s-]?|0)?([6-9]\d{9})(?!\d)", t)
        if m:
            put("phone", m.group(1), _excerpt(t, m.start(), m.end()))
        m = re.search(r"\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)+", t, re.I)
        if m:
            put("website", m.group(0).lower(), _excerpt(t, m.start(), m.end()))

    name = (_after(t, r"Legal\s+Name(?:\s+of\s+(?:the\s+)?Business)?")
            or _after(t, r"Name\s+of\s+(?:the\s+)?(?:Enterprise|Company|Firm|LLP|Applicant)")
            or None)
    if not name:
        m = re.search(r"(?:hereby\s+certify\s+that|certified\s+that)\s+(?:M/s\.?\s*)?(.{3,120}?)\s+is\s+(?:this\s+day\s+)?incorporated", t, re.I | re.S)
        if m:
            name = (" ".join(m.group(1).split()), _excerpt(t, m.start(), m.end()))
    if not name and category == "pan":
        m = re.search(r"Name\s*\n\s*(.+)", t, re.I)
        if m:
            name = (m.group(1).strip(), _excerpt(t, m.start(), m.end()))
    if name:
        put("legal_name", _clean_name(name[0]), name[1])
    c = _after(t, r"Constitution\s+of\s+Business|Type\s+of\s+Organi[sz]ation|Constitution")
    if c:
        low = c[0].lower()
        for word, kind in CONSTITUTION:
            if word in low:
                put("entity_type", kind, c[1])
                break
    addr = (_after(t, r"Address\s+of\s+Principal\s+Place\s+of\s+Business", 300, 4)
            or _after(t, r"(?:Official\s+)?Address\s+of\s+(?:the\s+)?(?:Enterprise|Unit)", 300, 4)
            or _after(t, r"Registered\s+(?:Office\s+)?Address|Address\s+of\s+(?:the\s+)?Registered\s+Office", 300, 4))
    if addr:
        put("registered_address", addr[0], addr[1])
    d = (_date_after(t, r"Date\s+of\s+Incorporation") or _date_after(t, r"Date\s+of\s+Registration|Date\s+of\s+Commencement")
         or _date_after(t, r"incorporated\s+on"))
    if not d and category == "incorporation":
        m = re.search(r"this\s+(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+)\s*,?\s*(\d{4})", t, re.I)
        if m:
            pd = parse_date(f"{m.group(1)} {m.group(2)} {m.group(3)}")
            if pd.value:
                d = (pd.value.isoformat(), _excerpt(t, m.start(), m.end()))
    if d and category in ("incorporation", "registration", "gst"):
        put("incorporation_date", d[0], d[1])
    return out


# ------------------------------------------------------------------- credential values
def credential(text: str, category: str) -> dict:
    t = _flat(text)
    out: dict = {"fields": {}, "excerpts": {}, "rows": []}
    f, ex = out["fields"], out["excerpts"]

    def put(key, got):
        if got and key not in f:
            f[key], ex[key] = got[0], got[1]

    out["issue_date"] = (_date_after(t, r"Date\s+of\s+Issue|Issue\s+Date|Issued\s+on|Dated|Date\s+of\s+Certificate")
                         or (None, None))[0] or _first_date(t)
    out["expiry_date"] = (_date_after(t, r"Valid(?:ity)?\s+(?:up\s*to|upto|till|until|through)|Validity\s+From\s*:?\s*[\d/.-]+\s+To|"
                                            r"Expiry\s+Date|Date\s+of\s+Expiry|Expires\s+on|Valid\s+To") or (None, None))[0]
    if out["expiry_date"] and out["issue_date"] and out["expiry_date"] <= out["issue_date"]:
        out["expiry_date"] = None  # an "expiry" before the issue date is a start date read wrongly
    m = re.search(r"M/s\.?\s*([A-Z][A-Za-z0-9&.,'()\- ]{2,120})", t)
    out["subject_name_on_document"] = _clean_name(m.group(1)) if m else None
    out["issuer"] = None
    if category == "pan":
        out["issuer"] = "Income Tax Department, Govt. of India"
    elif category == "gst":
        out["issuer"] = "Goods and Services Tax, Govt. of India"
    elif category == "incorporation":
        m = re.search(r"Registrar\s+of\s+Companies(?:\s*[,-]\s*|\s+)([A-Z][A-Za-z ]{2,30})?", t)
        out["issuer"] = ("Registrar of Companies" + (f", {m.group(1).strip()}" if m and m.group(1) else "")) if m else "Registrar of Companies"
    elif category == "registration" and re.search(r"UDYAM", t, re.I):
        out["issuer"] = "Ministry of MSME, Govt. of India"
    else:
        for ln in t.split("\n")[:8]:  # letterhead: first prominent line
            s = ln.strip()
            letters = re.sub(r"[^A-Za-z]", "", s)
            if len(letters) >= 8 and sum(ch.isupper() for ch in letters) / len(letters) > 0.7 and not re.search(r"CERTIFICATE|TO WHOM", s, re.I):
                out["issuer"] = s.strip(" :-|")[:150]
                break

    if category in ("completion_certificate", "performance_certificate", "work_order", "agreement", "executed_quantity"):
        put("work_name", _after(t, r"Name\s+of\s+(?:the\s+)?(?:Work|Project)|Sub(?:ject)?\s*[:.]|Work\s*[:]", 300, 3))
        if out["issuer"]:
            f.setdefault("employer", out["issuer"])
        put("executed_value", _amount_after(t, r"(?:gross\s+|total\s+|final\s+)?value\s+of\s+work\s+(?:done|executed|completed)|executed\s+value|final\s+bill\s+(?:amount|value)|total\s+cost\s+of\s+(?:the\s+)?work"))
        put("value", _amount_after(t, r"contract\s+value|agreement\s+(?:amount|value)|tendered\s+(?:amount|cost)|accepted\s+(?:amount|cost|value)|value\s+of\s+(?:the\s+)?(?:work\s+order|contract)|estimated\s+cost"))
        put("completion_date", _date_after(t, r"(?:actual\s+)?date\s+of\s+(?:actual\s+)?completion|completed\s+on|completion\s+date"))
        put("start_date", _date_after(t, r"date\s+of\s+(?:start|commencement)|commenced\s+on|start\s+date"))
        put("agreement_no", _after(t, r"Agreement\s+No\.?|Contract\s+No\.?|Work\s+Order\s+No\.?|LOA\s+No\.?", 80))
        if category in ("completion_certificate", "performance_certificate"):
            f.setdefault("status", "completed")
    if category in ("turnover_certificate", "audited_accounts", "net_worth"):
        scale = Decimal(1)
        sm = re.search(r"\(?\s*(?:Rs\.?|₹|INR|Amount)?\s*in\s+(lakhs?|lacs?|crores?|thousands?)\s*\)?", t, re.I)
        if sm:
            w = sm.group(1).lower()
            scale = Decimal(10000000) if w.startswith("cr") else Decimal(1000) if w.startswith("th") else Decimal(100000)
        seen = set()
        for m in re.finditer(r"(?:F\.?\s*Y\.?\s*)?\b(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})\b[^\n\d]{0,40}?((?:Rs\.?|₹)?\s*\d[\d,]*(?:\.\d+)?)", t):
            y1, y2 = int(m.group(1)), int(m.group(2)[-2:])
            if y2 != (y1 + 1) % 100:
                continue
            fy = f"{y1}-{y2:02d}"
            amt = parse_indian_amount(m.group(3))
            if amt is None or fy in seen or amt < 1:
                continue
            seen.add(fy)
            out["rows"].append({"fy": fy, "turnover": _money(amt * scale), "excerpt": " ".join(m.group(0).split())})
        out["rows"].sort(key=lambda r: r["fy"], reverse=True)
        if sm:
            out["notes"] = [f"Amounts on the document are in {sm.group(1).lower()}; converted to rupees."]
        nw = _amount_after(t, r"net\s*-?\s*worth")
        if nw and category != "turnover_certificate":
            v = parse_indian_amount(nw[0])
            put("net_worth", (_money(v * scale) if sm and v is not None and "," not in nw[0] else nw[0], nw[1]))
        put("as_of", _date_after(t, r"as\s+on|as\s+at"))
        if out["rows"] and category != "net_worth":
            f.setdefault("fy", out["rows"][0]["fy"])
            f.setdefault("turnover", out["rows"][0]["turnover"])
    if category == "solvency":
        put("amount", _amount_after(t, r"solvent\s+(?:to\s+the\s+extent\s+of|for|up\s*to)|solvency\s+(?:of|for)|to\s+the\s+extent\s+of"))
    if category == "registration":
        m = re.search(r"\bClass\s*[-:]?\s*((?:[IVX]{1,4}|[A-E]{1,2})(?:\s*\(\s*[A-Z]\s*\))?)", t)
        if m:
            f["class"], ex["class"] = m.group(1).replace(" ", ""), _excerpt(t, m.start(), m.end())
        put("registration_no", _after(t, r"Registration\s+(?:No|Number)\.?|Enlistment\s+No\.?|UDYAM\s+REGISTRATION\s+NUMBER", 60))
        if out["issuer"]:
            f.setdefault("authority", out["issuer"])
    if category == "quality_certification":
        m = re.search(r"ISO\s*(9001|14001|45001|27001)(?:\s*:\s*(\d{4}))?", t, re.I)
        if m:
            out["title"] = f"ISO {m.group(1)}{':' + m.group(2) if m.group(2) else ''} certificate"
    if category == "personnel":
        put("person_name", _after(t, r"Name", 80))
        put("qualification", _after(t, r"(?:Educational\s+)?Qualifications?", 120))
        m = re.search(r"(\d{1,2})\+?\s*(?:years|yrs)\s+(?:of\s+)?experience", t, re.I)
        if m:
            f["experience_years"], ex["experience_years"] = m.group(1), _excerpt(t, m.start(), m.end())
    return out


def read_document(data: bytes, filename: str, ocr: OcrAdapter | None = None) -> dict:
    """Everything the screens need to fill the forms; nothing is stored."""
    r = read_text(data, filename, ocr)
    category, score = classify(r.text) if r.text else ("other", 0)
    cred = credential(r.text, category) if r.text else {"fields": {}, "excerpts": {}, "rows": []}
    title = cred.pop("title", None) or TITLES.get(category, "Document")
    if category == "registration":
        title = ("Udyam (MSME) registration" if re.search(r"UDYAM", r.text, re.I)
                 else "EPF registration" if re.search(r"Provident\s+Fund", r.text, re.I)
                 else "ESI registration" if re.search(r"State\s+Insurance", r.text, re.I) else "Contractor registration")
    found = bool(r.text.strip())
    return {
        "reader": READER_VERSION, "filename": filename, "kind": r.kind, "pages_total": r.pages_total,
        "pages_read": r.pages_read, "read_by": r.read_by, "notes": r.notes + cred.pop("notes", []),
        "readable": found, "category": category, "category_sure": score >= 5, "title": title,
        "issuer": cred.pop("issuer", None), "issue_date": cred.pop("issue_date", None),
        "expiry_date": cred.pop("expiry_date", None), "subject_name_on_document": cred.pop("subject_name_on_document", None),
        "fields": cred["fields"], "excerpts": cred["excerpts"], "rows": cred["rows"],
        "firm": firm_details(r.text, category) if found else {},
        "text_preview": r.text[:2000],
    }


def compare_with(result: dict, entity) -> dict:
    """Mark each firm detail as new / same / different from what the firm already has."""
    prof = entity.profile or {}
    for key, item in result["firm"].items():
        cur = getattr(entity, key, None) if item["target"] == "core" else prof.get(key)
        cur = (cur or "").strip() if isinstance(cur, str) else cur
        item["current"] = cur or None
        same = bool(cur) and _norm(str(cur)) == _norm(str(item["value"]))
        item["state"] = "same" if same else "new" if not cur else "different"
    return result


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("private", "pvt").replace("limited", "ltd"))
