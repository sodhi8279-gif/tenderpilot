"""Deterministic, source-anchored requirement extraction ("heuristic-v1").

This genuinely analyses the uploaded text (no fixtures). Each candidate carries the exact
excerpt, page, clause number and a confidence. Reviewers confirm before material use.
An optional AI provider (services/ai.py) may add candidates; their excerpts must exist
verbatim in the page text or they are discarded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .dates import detect_timezone, parse_date
from .money import parse_indian_amount
from .technical import _strip_marker, extract_experience, extract_technical, is_fragment, leading_rows, open_section_at_end

# Bump when the reader changes: documents read by an older reader are read again on the next start.
READER_VERSION = "reader-3"  # reader-3: personnel / machinery / quantity tables continued on the next page

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "single": 1}
NUM = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|single)"
AMT = r"(?:Rs\s?\.{0,2}|INR|₹)\s*[\d,]+(?:\.\d+)?\s*(?:crores?|cr\.?|lakhs?|lacs?)?"
CLAUSE_RE = re.compile(r"^\s*(?:clause\s+)?((?:\d{1,2}\.){0,4}\d{1,3}(?:\.?\([a-z0-9]+\))?|[A-Z]\.\d+(?:\.\d+)*|\([a-z]\))[\s.:)-]", re.I)


def wn(s: str) -> int:
    s = s.lower()
    return WORDNUM.get(s, int(s) if s.isdigit() else 0)


@dataclass
class Candidate:
    key: str
    category: str
    title: str
    value: dict
    excerpt: str
    page_index: int
    clause_no: str | None
    confidence: float
    mandatory: bool = True
    nature: str = "extracted"
    notes: list[str] = field(default_factory=list)
    extractor: str = "heuristic-v1"


def _sentence_around(text: str, start: int, end: int, limit: int = 320) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n\n", 0, start))
    left = 0 if left < 0 else left + 1
    right_candidates = [i for i in (text.find(". ", end), text.find("\n\n", end)) if i >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    left = max(left, start - limit // 2)
    right = min(right, end + limit // 2)
    return " ".join(text[left:right].split())


def _clause_for(text: str, pos: int) -> str | None:
    """Clause number of the line containing `pos`, else the nearest numbered line above it."""
    line_start = text.rfind(chr(10), 0, pos) + 1
    line_end = text.find(chr(10), pos)
    current = text[line_start: line_end if line_end >= 0 else len(text)]
    for line in [current] + list(reversed(text[:line_start].splitlines()[-25:])):
        m = CLAUSE_RE.match(line)
        if m and len(line.strip()) > len(m.group(1)) + 1:
            return m.group(1).rstrip(".")
    return None


PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    ("deadline.bid_submission", "deadline", "Last date and time for bid submission",
     re.compile(r"(?:last|closing|end)\s+date[^\n]{0,60}?(?:submission|receipt|upload)[^\n]{0,40}?[:\-–]?\s*([^\n]{0,80})", re.I)),
    ("deadline.bid_opening", "deadline", "Technical bid opening date",
     re.compile(r"(?:date|time)[^\n]{0,30}opening[^\n]{0,40}?[:\-–]\s*([^\n]{0,80})", re.I)),
    ("deadline.prebid", "deadline", "Pre-bid meeting",
     re.compile(r"pre[\s-]?bid\s+(?:meeting|conference)[^\n]{0,60}?[:\-–]\s*([^\n]{0,80})", re.I)),
    ("commercial.estimate", "commercial", "Estimated cost put to tender",
     re.compile(r"(?:estimated\s+(?:cost|value)|estimate\s+put\s+to\s+tender|tender\s+value)[^\n]{0,60}?(" + AMT + ")", re.I)),
    ("commercial.emd", "commercial", "Bid security / EMD",
     re.compile(r"(?:earnest\s+money(?:\s+deposit)?|\bEMD\b|bid\s+security)[^\n]{0,80}?(" + AMT + ")", re.I)),
    ("commercial.tender_fee", "commercial", "Tender document fee",
     re.compile(r"(?:tender\s+(?:document\s+)?fee|cost\s+of\s+(?:tender|bid)\s+document)[^\n]{0,60}?(" + AMT + ")", re.I)),
    ("commercial.bid_validity", "commercial", "Bid validity",
     re.compile(r"(?:bid|tender|offer)s?\s+(?:shall\s+)?(?:remain\s+)?valid(?:ity)?[^\n]{0,40}?(\d{2,3})\s*days", re.I)),
    ("commercial.completion_period", "commercial", "Period of completion",
     re.compile(r"(?:period\s+of\s+completion|completion\s+period|time\s+(?:allowed\s+)?for\s+completion|stipulated\s+period)[^\n]{0,60}?(\d{1,3})\s*(months?|days|years?)", re.I)),
    ("commercial.performance_security", "commercial", "Performance security",
     re.compile(r"performance\s+(?:security|guarantee|bank\s+guarantee)[^\n]{0,80}?(\d{1,2}(?:\.\d+)?)\s*%", re.I)),
    ("commercial.ld", "commercial", "Liquidated damages",
     re.compile(r"(?:liquidated\s+damages|compensation\s+for\s+delay)[^\n]{0,100}?(\d(?:\.\d+)?)\s*%[^\n]{0,80}?(?:maximum|subject\s+to|limited\s+to|ceiling)[^\n]{0,30}?(\d{1,2}(?:\.\d+)?)\s*%", re.I)),
    ("commercial.retention", "commercial", "Retention money",
     re.compile(r"retention(?:\s+money)?[^\n]{0,60}?(\d{1,2}(?:\.\d+)?)\s*%", re.I)),
    ("commercial.defect_liability", "commercial", "Defect liability period",
     re.compile(r"defects?\s+liability\s+period[^\n]{0,40}?(\d{1,3})\s*(months?|years?)", re.I)),
    ("commercial.price_adjustment", "commercial", "Price adjustment / variation",
     re.compile(r"(price\s+(?:variation|adjustment|escalation)\s+(?:clause\s+)?(?:shall|will|is)\s*(?:not\s+)?(?:be\s+)?(?:applicable|payable|admissible))", re.I)),
    ("fin.avg_turnover", "financial", "Minimum average annual turnover",
     re.compile(r"average\s+annual\s+(?:financial\s+)?turnover[^\n]{0,160}?(" + AMT + ")", re.I | re.S)),
    ("fin.net_worth", "financial", "Net worth",
     re.compile(r"net\s*worth[^\n]{0,80}?(positive|" + AMT + ")", re.I)),
    ("fin.solvency", "financial", "Solvency certificate",
     re.compile(r"solvency\s+certificate[^\n]{0,80}?(" + AMT + ")", re.I)),
    ("fin.bid_capacity", "financial", "Bid capacity",
     re.compile(r"bid(?:ding)?\s+capacity[^\n]{0,200}?(?:A\s*[x×*]\s*N\s*[x×*]\s*(\d(?:\.\d+)?)\s*-\s*B|=\s*\(?\s*A\s*[x×*]\s*N\s*[x×*]\s*(\d(?:\.\d+)?))", re.I | re.S)),
    ("qual.cutoff", "qualification", "Qualification reference date",
     re.compile(r"(?:as\s+on|ending|up\s*to)\s+(the\s+(?:last\s+(?:date|day)[^\n.]{0,80}|date\s+of\s+(?:bid\s+)?(?:submission|opening)[^\n.]{0,40}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))", re.I)),
    # "single entity / sole bidder or a Joint venture" states that a JV may bid
    ("jv.permitted", "jv", "Joint venture permission",
     re.compile(r"(?:single\s+entity|sole\s+(?:bidder|proprietor|contractor)|individual\s+(?:firm|bidder|entity))[^.]{0,60}?\bor\s+(?:a\s+|as\s+a\s+)?"
                r"(joint\s+venture|consortium|JV)\b", re.I)),
    # A ban on *foreign* JVs ("joint ventures with foreign partners ... are not permitted") is not a ban on JVs.
    ("jv.permitted", "jv", "Joint venture permission",
     re.compile(r"(joint\s+venture|consortium|\bJV\b)s?(?![^\n.]{0,70}?foreign)[^\n.]{0,60}?\b(not\s+(?:be\s+)?(?:permitted|allowed|accepted)|(?:is|are|shall\s+be)\s+(?:permitted|allowed|accepted))", re.I)),
    ("jv.max_members", "jv", "Maximum JV members",
     re.compile(r"(?:joint\s+venture|consortium|JV)[^\n.]{0,80}?(?:not\s+more\s+than|maximum(?:\s+of)?|up\s+to)\s+" + NUM + r"\s+(?:\(\d\)\s+)?(?:members|partners|firms)", re.I)),
    ("jv.lead_share", "jv", "Lead member minimum share",
     re.compile(r"lead\s+(?:member|partner)[^\n.]{0,80}?(?:not\s+less\s+than|minimum(?:\s+of)?|at\s+least)\s+(\d{1,2})\s*%", re.I)),
    ("envelope.structure", "envelope", "Bid envelope structure",
     re.compile(r"\b(single|two|three|one|2|3)[\s-]*(?:bid|envelope|cover|packet|stage)[\s-]*(?:system|bid)", re.I)),
    ("precedence.order", "precedence", "Order of precedence of documents",
     re.compile(r"(order\s+of\s+precedence|priority\s+of\s+(?:contract\s+)?documents)[^\n]{0,20}", re.I)),
]

SIMILAR_WORK_RE = re.compile(
    r"(?P<n>" + NUM + r")\s+similar\s+(?:completed\s+)?works?\s+(?:[a-z]{1,5}\s+(?=each\b))?(?:each\s+)?(?:of\s+value\s+|costing\s+|of\s+)?(?:[‘’'\x22]?not\s+less\s+than\s+)?"
    r"(?:(?P<pct>\d{1,3})\s*%\s*(?:of\s+(?:the\s+)?estimated\s+(?:cost|value))?|(?P<amt>" + AMT + "))", re.I)
ESTIMATE_TABLE_HDR = re.compile(r"estimated\s+cost\s+(?:of\s+(?:the\s+)?work|put\s+to\s+tender)|advertised\s+(?:cost|value)|"
                                r"estimated\s+(?:value|amount)\s+of\s+(?:the\s+)?work", re.I)
ESTIMATE_TABLE_VAL = re.compile(r"(?<![\d.,])(?:(?:Rs\s?\.{0,2}|INR|₹)\s*)?\d+(?:,\d{2,3})*(?:\.\d+)?\s*(?:crores?|cr\b\.?|lakhs?|lacs?)", re.I)
CLAUSE_START_RE = re.compile(r"(?:(?<=\s)|^)(?<!Rs\.\s)(?<!Rs\s)(?<!INR\s)\d{1,2}(?:\.\d{1,2})+\s+(?=[A-Z])(?!(?:Crores?|Lakhs?|Lacs?|Million)\b)")
PERIOD_RE = re.compile(r"(?:during\s+the\s+)?last\s+" + NUM + r"\s+(?:\(\d+\)\s+)?(?:financial\s+|completed\s+|consecutive\s+)?years?", re.I)

DOC_REQUIREMENTS = [
    ("doc.pan", "PAN card", r"\bPAN\b", "pan"),
    ("doc.gst", "GST registration certificate", r"\bGST(?:IN)?\b[^\n]{0,30}(?:registration|certificate|number)", "gst"),
    ("doc.epf", "EPF registration", r"\b(?:EPF|PF)\s+registration", "registration"),
    ("doc.esi", "ESI registration", r"\bESI(?:C)?\s+registration", "registration"),
    ("doc.iso9001", "ISO 9001 certificate", r"ISO\s*9001", "quality_certification"),
    ("doc.contractor_registration", "Contractor registration", r"registered\s+(?:contractor|in\s+class)|contractor\s+registration|enlistment", "registration"),
    ("doc.electrical_licence", "Electrical contractor licence", r"electrical\s+(?:contractor\s+)?licen[cs]e", "registration"),
    ("doc.poa", "Power of attorney for signatory", r"power\s+of\s+attorney", "power_of_attorney"),
    ("doc.affidavit", "Affidavit / undertaking", r"\baffidavit\b|\bundertaking\b", "declaration"),
    ("doc.audited_accounts", "Audited balance sheets", r"audited\s+(?:balance\s+sheets?|accounts|financial\s+statements)", "audited_accounts"),
    ("doc.oem_authorization", "OEM / manufacturer authorization", r"(?:OEM|manufacturer'?s?)\s+authori[sz]ation", "oem_authorization"),
    ("doc.blacklisting", "Non-blacklisting declaration", r"black[\s-]?list", "declaration"),
]


def _conf(base: float, page_conf: float | None) -> float:
    return round(min(base, page_conf if page_conf is not None else base), 3)


def extract_from_page(text: str, page_index: int, page_conf: float | None) -> list[Candidate]:
    out: list[Candidate] = []
    seen_keys: set[str] = set()
    # Same length as `text`, so match positions still index the original for clause lookup.
    flat = text.replace(chr(13), " ").replace(chr(10), " ")
    for key, cat, title, rx in PATTERNS:
        if key in seen_keys:
            continue  # an earlier pattern for the same fact already matched on this page
        m = rx.search(flat)
        if not m:
            continue
        excerpt = _sentence_around(flat, m.start(), m.end())
        clause = _clause_for(text, m.start())
        value: dict = {}
        base = 0.9
        notes: list[str] = []
        g = [x for x in m.groups() if x]
        if cat == "deadline":
            pd = parse_date(g[0] if g else excerpt)
            if pd.value is None:
                continue
            tz = detect_timezone(excerpt)
            value = {"date": pd.value.isoformat(), "time": pd.time_value.isoformat() if pd.time_value else None,
                     "timezone": tz or "Asia/Kolkata", "timezone_explicit": bool(tz), "original": pd.raw.strip()[:120]}
            if pd.note:
                notes.append(pd.note)
            if pd.ambiguous or not pd.time_value:
                base = 0.7
                notes.append("Time missing or date ambiguous; confirm.")
        elif key in ("commercial.estimate", "commercial.emd", "commercial.tender_fee", "fin.avg_turnover", "fin.solvency"):
            amt = parse_indian_amount(g[0])
            if amt is None:
                continue
            value = {"amount": str(amt), "currency": "INR", "original": g[0]}
            if key == "fin.avg_turnover":
                pm = PERIOD_RE.search(flat[m.start(): m.end()]) or PERIOD_RE.search(flat[m.end(): m.end() + 60])
                value["years"] = wn(pm.group(1)) if pm else None
                if not pm:
                    base = 0.75
                    notes.append("Number of years for averaging not found nearby.")
                if re.search(r"best\s+" + NUM, excerpt, re.I):
                    value["method"] = "best_n_of_m"
                    bm = re.search(r"best\s+" + NUM + r"\s+(?:years?\s+)?(?:out\s+)?of\s+(?:the\s+)?(?:last\s+)?" + NUM, excerpt, re.I)
                    if bm:
                        value["best"], value["years"] = wn(bm.group(1)), wn(bm.group(2))
                else:
                    value["method"] = "last_n"
        elif key == "fin.net_worth":
            value = {"positive_only": True} if g[0].lower() == "positive" else {"amount": str(parse_indian_amount(g[0])), "currency": "INR"}
        elif key == "fin.bid_capacity":
            mult = g[0] if g else None
            value = {"formula": "A*N*M-B", "M": mult}
            base = 0.8
        elif key == "commercial.bid_validity":
            value = {"days": int(g[0])}
        elif key in ("commercial.completion_period", "commercial.defect_liability"):
            value = {"value": int(g[0]), "unit": g[1].lower().rstrip("s") if len(g) > 1 else "month"}
        elif key in ("commercial.performance_security", "commercial.retention"):
            value = {"percent": g[0]}
        elif key == "commercial.ld":
            value = {"rate_percent": g[0], "max_percent": g[1] if len(g) > 1 else None}
        elif key == "commercial.price_adjustment":
            value = {"applicable": "not" not in g[0].lower()}
        elif key == "qual.cutoff":
            phrase = " ".join(g[0].split())
            pd = parse_date(phrase)
            value = {"rule_text": phrase, "date": pd.value.isoformat() if pd.value else None}
            base = 0.7
            notes.append("Confirm which date the qualification criteria are measured against.")
        elif key == "jv.permitted":
            value = {"permitted": "not" not in g[-1].lower()}
        elif key == "jv.max_members":
            value = {"max_members": wn(g[0])}
        elif key == "jv.lead_share":
            value = {"min_percent": g[0]}
        elif key == "envelope.structure":
            word = g[0].lower()
            value = {"envelopes": {"single": 1, "one": 1, "two": 2, "2": 2, "three": 3, "3": 3}.get(word, 2)}
        elif key == "precedence.order":
            value = {"found": True}
            base = 0.6
            notes.append("Order-of-precedence clause located; reviewer must record the ranking.")
        out.append(Candidate(key, cat, title, value, excerpt, page_index, clause, _conf(base, page_conf), notes=notes))
        seen_keys.add(key)

    # Estimated cost printed in the NIT table: the figure sits under the column heading without "Rs."
    if "commercial.estimate" not in seen_keys and (hm := ESTIMATE_TABLE_HDR.search(flat)):
        vm = ESTIMATE_TABLE_VAL.search(flat, hm.end(), hm.end() + 700)
        amt = parse_indian_amount(vm.group(0)) if vm else None
        if amt:
            out.append(Candidate("commercial.estimate", "commercial", "Estimated cost put to tender",
                                 {"amount": str(amt), "currency": "INR", "original": " ".join(vm.group(0).split())},
                                 f"{' '.join(hm.group(0).split())} … {' '.join(vm.group(0).split())}", page_index,
                                 _clause_for(text, vm.start()), _conf(0.65, page_conf),
                                 notes=["Read from the NIT table (column heading and figure are apart); confirm the amount."]))
            seen_keys.add("commercial.estimate")

    # General / specific experience sections (nature of work, sizes, alternatives, JV note)
    exp_cands, exp_spans = extract_experience(text, page_index, page_conf, _clause_for, Candidate)
    out.extend(exp_cands)

    for m in SIMILAR_WORK_RE.finditer(flat):
        if any(s <= m.start() < e for s, e in exp_spans):
            continue  # already read, with its nature of work, as part of the experience section
        n = wn(m.group("n"))
        excerpt = _sentence_around(flat, m.start(), m.end(), 400)
        val: dict = {"count": n}
        if m.group("pct"):
            val["percent_of_estimate"] = m.group("pct")
        if m.group("amt"):
            val["amount"] = str(parse_indian_amount(m.group("amt")))
        # nearest period phrase to this clause (avoids picking up a neighbouring clause's period)
        # Search only from the start of this numbered clause, so "turnover during the last 3 financial years" in
        # clause 10.1 is not taken as the experience period of clause 10.2.
        lo = max(0, m.start() - 300)
        starts = [b.start() for b in CLAUSE_START_RE.finditer(flat, lo, m.start())]
        if starts:
            lo = starts[-1]
        periods = list(PERIOD_RE.finditer(flat[lo: m.end() + 300]))
        pm = min(periods, key=lambda p: abs(lo + p.start() - m.start())) if periods else None
        val["period_years"] = wn(pm.group(1)) if pm else None
        val["allow_substantial_completion"] = bool(re.search(r"substantially\s+completed", excerpt, re.I))
        out.append(Candidate(f"exp.similar_work.{n}", "experience", f"{n} similar work(s) of required value",
                             val, excerpt, page_index, _clause_for(text, m.start()),
                             _conf(0.85 if pm else 0.7, page_conf),
                             notes=[] if pm else ["Qualifying period not found near the clause."]))

    for key, title, pat, evcat in DOC_REQUIREMENTS:
        m = re.search(pat, flat, re.I)
        if m and key not in seen_keys:
            excerpt = _sentence_around(flat, m.start(), m.end(), 240)
            out.append(Candidate(key, "document", title, {"evidence_category": evcat}, excerpt, page_index,
                                 _clause_for(text, m.start()), _conf(0.75, page_conf)))

    # Technical qualification criteria (personnel, plant & machinery, quantities, registration class, JV split)
    out.extend(extract_technical(text, flat, page_index, page_conf, _clause_for, _sentence_around, Candidate))
    return out


_TABLE_KEYS = ("tech.personnel.", "tech.equipment.", "tech.quantity.")


def extract_continuations(pages: list[tuple[str, int, float | None]], base: list[Candidate]) -> list[Candidate]:
    """Rows of a personnel / machinery / quantity table that run onto the next page.

    Each page is read on its own by `extract_from_page`, so rows on the following page, below no heading, would be
    missed. Here the heading still open at the bottom of page N is joined to the leading rows of page N+1 and read
    again; only table rows are kept, anchored on page N+1 where they are printed. A row cut by the page break is
    joined to its first half and replaces the partial reading. Rows equal to one already read are dropped; a different
    row with the same post or machine name gets its own key (as on a single page)."""
    out: list[Candidate] = []
    keys = {c.key: c for c in base}
    for (t1, i1, _c1), (t2, i2, c2) in zip(pages, pages[1:]):
        if i2 != i1 + 1:
            continue
        open_ = open_section_at_end(t1)
        if not open_:
            continue
        head, last = open_
        rows = leading_rows(t2)
        if not rows:
            continue
        joined = bool(last) and is_fragment(rows[0])
        text = "\n".join([head] + ([last] if joined else []) + rows)
        flat = text.replace(chr(10), " ")
        for c in extract_technical(text, flat, i2, c2, _clause_for, _sentence_around, Candidate):
            if not c.key.startswith(_TABLE_KEYS):
                continue
            c.notes.append(f"Row continues the table '{' '.join(head.split())[:80]}' from page {i1 + 1}.")
            prior = keys.get(c.key)
            if joined and last and " ".join(_strip_marker(last).split())[:40] in c.excerpt:
                c.notes.append(f"The row starts at the foot of page {i1 + 1} and ends on page {i2 + 1}.")
                if prior is not None:
                    base.remove(prior)  # the partial first half read on page N
                keys[c.key] = c
                out.append(c)
                continue
            if prior is not None:
                if prior.value == c.value:
                    continue
                n, k = 2, f"{c.key}_2"
                while k in keys:
                    n, k = n + 1, f"{c.key}_{n + 1}"
                c.key = k
            keys[c.key] = c
            out.append(c)
    return out


def merge_candidates(cands: list[Candidate]) -> list[Candidate]:
    """Keep the first, most confident occurrence per key; record conflicting values as notes."""
    best: dict[str, Candidate] = {}
    for c in cands:
        cur = best.get(c.key)
        if cur is None:
            best[c.key] = c
            continue
        if c.key == "jv.criteria_split":
            # The JV clause is often split over pages (sharing rule on one, lead minimum on another): combine them.
            added = {k: v for k, v in c.value.items() if k not in cur.value}
            clash = {k for k, v in c.value.items() if k in cur.value and cur.value[k] != v}
            if added:
                cur.value = {**cur.value, **added}
                cur.excerpt = f"{cur.excerpt} … (page {c.page_index + 1}) {c.excerpt}"[:600]
            if clash:
                cur.notes.append(f"Different JV sharing wording also on page {c.page_index + 1}: "
                                 f"{ {k: c.value[k] for k in clash} }")
                cur.confidence = min(cur.confidence, 0.6)
            continue
        if cur.value != c.value and c.category not in ("document",):
            cur.notes.append(f"Different value also found on page {c.page_index + 1}: {c.value}")
            cur.confidence = min(cur.confidence, 0.6)
        elif c.confidence > cur.confidence:
            best[c.key] = c
    return list(best.values())


def find_bbox(doc, page_index: int, excerpt: str) -> list[float] | None:
    """Locate the excerpt on a PDF page; returns [x0,y0,x1,y1] of the first matched span."""
    try:
        page = doc.load_page(page_index)
    except Exception:  # noqa: BLE001
        return None
    words = excerpt.split()
    for n in (10, 7, 5, 3):
        if len(words) < n:
            continue
        for start in range(0, min(len(words) - n + 1, 6)):
            needle = " ".join(words[start:start + n])
            rects = page.search_for(needle)
            if rects:
                r = rects[0]
                for extra in rects[1:4]:
                    if abs(extra.y0 - r.y1) < 30:
                        r = r | extra
                return [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]
    return None


def amount_or_none(v) -> Decimal | None:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None
