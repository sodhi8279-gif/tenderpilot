"""Technical qualification criteria: extraction and normalisation ("technical-v1").

Reads the parts of a tender that the financial patterns do not cover:
  * key personnel (role, number, minimum qualification, discipline, years of experience)
  * key plant and machinery (type, number, minimum capacity, owned or leased)
  * minimum physical quantities executed (item, quantity, unit, "in any one year" or cumulative)
  * contractor registration class
  * the tender's own definition of "similar work"
  * how JV partners may combine to meet the criteria (lead / each member percentages, combined, lead alone)

Everything here is a deterministic heuristic. Each candidate carries its excerpt, page and clause so a
reviewer confirms it against the source before it becomes a rule. Nothing is assumed when a value is
not printed: the field stays empty and the rule stays ambiguous until a reviewer enters it.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "single": 1}
NUMW = r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)"


def _wn(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return WORDNUM.get(s)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:50] or "item"


def to_decimal(s) -> Decimal | None:
    if s is None or s == "":
        return None
    try:
        return Decimal(str(s).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


# ------------------------------------------------------------------ qualifications
QUAL_LEVELS = ["iti", "diploma", "graduate", "postgraduate"]
_QUAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("postgraduate", re.compile(r"\bM\.?\s?Tech\b|\bM\.\s?E\b\.?|\bPost[\s-]?Graduate\b|\bM\.?\s?Sc\b|\bPh\.?\s?D\b|\bMBA\b", re.I)),
    ("graduate", re.compile(r"\bB\.?\s?Tech\b|\bB\.\s?E\b\.?|\bB\.?\s?Sc\.?\s*\(?\s*Engg|\bAMIE\b|\bGraduate\b|\bDegree\b|\bB\.?\s?Arch\b", re.I)),
    ("diploma", re.compile(r"\bDiploma\b", re.I)),
    ("iti", re.compile(r"\bI\.?T\.?I\b", re.I)),
]
_BE_UPPER = re.compile(r"\bBE\b|\bME\b(?=\s*[(/,]|\s+(?:in|Civil|Mech|Elec))")  # case-sensitive: "be"/"me" are English words
DISCIPLINES = {
    "civil": r"civil|structural",
    "mechanical": r"mechanical|\bmech\b",
    "electrical": r"electrical|\belec\b|\bEEE\b",
    "electronics": r"electronics|\bECE\b|instrumentation",
    "environmental": r"environment(?:al)?",
    "chemical": r"chemi(?:cal|stry)",
    "computer": r"computer|\bIT\b|information\s+technology",
    "geology": r"geolog",
    "survey": r"survey",
}


def qualification_levels(text: str | None) -> list[str]:
    """All qualification levels named in the text (e.g. 'B.E./Diploma' -> ['graduate', 'diploma'])."""
    text = text or ""
    found = [lvl for lvl, rx in _QUAL_PATTERNS if rx.search(text)]
    for m in _BE_UPPER.finditer(text):
        lvl = "graduate" if m.group(0) == "BE" else "postgraduate"
        if lvl not in found:
            found.append(lvl)
    return found


def min_qualification(text: str | None) -> str | None:
    """Lowest level named: 'B.E. or Diploma' accepts a diploma holder."""
    lv = qualification_levels(text)
    return min(lv, key=QUAL_LEVELS.index) if lv else None


def best_qualification(text: str | None) -> str | None:
    """Highest level a person holds ('Diploma, later B.Tech' -> graduate)."""
    lv = qualification_levels(text)
    return max(lv, key=QUAL_LEVELS.index) if lv else None


def disciplines(text: str | None) -> list[str]:
    text = text or ""
    return [d for d, pat in DISCIPLINES.items() if re.search(pat, text, re.I)]


# ------------------------------------------------------------------ equipment
# Specific names are listed before generic ones; the first match wins.
EQUIPMENT_CATALOGUE: dict[str, str] = {
    "batching_plant": r"batch(?:ing)?\s*(?:mix(?:ing)?\s*)?plant|\bRMC\s+plant",
    "grouting_equipment": r"grout|epoxy\s*injection|injection\s*(?:pump|equipment|machine)",
    "shotcrete_machine": r"shotcrete|gunit",
    "drilling_rig": r"drill(?:ing)?\s*rigs?|rock\s*drill|drilling\s*(?:machine|equipment)|\bDTH\b|wagon\s*drill",
    "formwork": r"form\s*work|shuttering|staging\s+material",
    "hot_mix_plant": r"hot\s*mix|drum\s*mix|asphalt\s*plant",
    "transit_mixer": r"transit\s*mixer",
    "concrete_pump": r"concrete\s*pump|boom\s*placer",
    "concrete_mixer": r"concrete\s*mixer|mixer\s*machine",
    "excavator": r"excavator|poclain",
    "backhoe_loader": r"backhoe|\bJCB\b|loader\s*cum\s*excavator",
    "wheel_loader": r"wheel\s*loader|front\s*end\s*loader",
    "crane": r"crane|\bhydra\b",
    "road_roller": r"roller|compactor",
    "paver": r"paver|paving\s+machine|finisher",
    "motor_grader": r"grader",
    "dozer": r"dozer",
    "tipper": r"tipper|dumper|dump\s*truck",
    "water_tanker": r"water\s*tanker|tanker",
    "hydro_test_pump": r"hydro(?:static)?\s*test(?:ing)?\s*pump",
    "dewatering_pump": r"dewatering",
    "pump": r"\bpumps?\b",
    "dg_set": r"\bD\.?\s?G\.?\s*sets?\b|diesel\s*generat|generator",
    "vibrator": r"vibrator",
    "air_compressor": r"compressor",
    "hdd_machine": r"\bHDD\b|horizontal\s+directional|trenchless",
    "pipe_jacking": r"pipe\s*jacking|micro[\s-]*tunnel",
    "piling_rig": r"piling\s*rig|rotary\s*rig|\brig\b",
    "welding_machine": r"welding",
    "crusher": r"crusher",
    "survey_instrument": r"total\s*station|auto\s*level|theodolite|survey\s+instrument",
    "testing_lab": r"(?:field|site)\s*lab|laboratory|testing\s+equipment",
    "bitumen_sprayer": r"bitumen\s*(?:sprayer|distributor|pressure)",
    "mixer": r"\bmixers?\b",
}
_EQUIP_NOUN = re.compile(
    r"plant|mixer|machine|excavator|crane|hydra|roller|compactor|paver|loader|dozer|grader|tipper|truck|dumper|pump|vibrator|"
    r"generator|D\.?G\.?\s*set|\brig\b|compressor|JCB|poclain|tanker|sprayer|crusher|welding|winch|trailer|tractor|\blab\b|"
    r"laboratory|equipment|breaker|drill|total\s*station|auto\s*level|HDD|finisher|jack|form\s*work|shuttering|grout|"
    r"shotcrete|gunit|injection", re.I)
# Words that make a list line a sentence about the equipment, not an item of it.
_EQUIP_SENTENCE = re.compile(r"^(?:the|in\s+case|following|documentary|proof|note|all|any|bidders?|tenderers?|contractors?)\b|"
                             r"\b(?:shall|should|must|may|will|is\s+required|are\s+required)\b", re.I)


def canonical_equipment(name: str | None) -> str | None:
    name = name or ""
    for key, pat in EQUIPMENT_CATALOGUE.items():
        if re.search(pat, name, re.I):
            return key
    return None


_STOP = {"of", "and", "the", "with", "for", "capacity", "minimum", "min", "no", "nos", "not", "less", "than", "or",
         "above", "type", "at", "least", "each", "number", "numbers", "set", "sets", "unit", "units", "an",
         "in", "on", "to", "by", "per", "hr", "hour", "cum", "tph", "kva", "kw", "hp", "mt", "owned", "leased", "hired"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", (s or "").lower()) if t not in _STOP and len(t) > 1}


def equipment_matches(required: str, offered: str) -> bool:
    a, b = canonical_equipment(required), canonical_equipment(offered)
    if a or b:
        return a == b
    ra, ob = _tokens(required), _tokens(offered)
    return bool(ra) and ra <= ob


# ------------------------------------------------------------------ units
# (pattern, base unit, factor to base). Order matters: rates before amounts, "lakh cum" before "cum".
_UNITS: list[tuple[str, str, Decimal]] = [
    (r"cum\s*/\s*h(?:ou)?r|cum\s+per\s+hour|m3\s*/\s*h(?:ou)?r|cu\.?\s*m\s*/\s*h(?:ou)?r", "cum/hr", Decimal(1)),
    (r"tph|tonnes?\s*/\s*h(?:ou)?r|tonnes?\s+per\s+hour", "TPH", Decimal(1)),
    (r"lakh\s*cum|lakh\s*cu\.?\s*m", "cum", Decimal(100000)),
    (r"lakh\s*sqm", "sqm", Decimal(100000)),
    (r"cum|cu\.?\s*m(?:tr|etre)?s?|m3|m³|cubic\s+met(?:er|re)s?", "cum", Decimal(1)),
    (r"sqm|sq\.?\s*m(?:tr|etre)?s?|m2|m²|square\s+met(?:er|re)s?", "sqm", Decimal(1)),
    (r"mt|m\.t\.?|metric\s+tonnes?|tonnes?|tons?|t", "MT", Decimal(1)),
    (r"kms?|kilomet(?:er|re)s?|rkm", "m", Decimal(1000)),
    (r"rmt|running\s+met(?:er|re)s?|r\.?m\.?|met(?:er|re)s?|mtrs?|m", "m", Decimal(1)),
    (r"kva", "kVA", Decimal(1)),
    (r"kw", "kW", Decimal(1)),
    (r"hp|h\.p\.", "HP", Decimal(1)),
    (r"kl|kilolit(?:er|re)s?", "litre", Decimal(1000)),
    (r"litres?|liters?|ltrs?", "litre", Decimal(1)),
    (r"mld", "MLD", Decimal(1)),
    (r"mm", "mm", Decimal(1)),
]


def norm_unit(unit: str | None) -> tuple[str | None, Decimal]:
    u = " ".join((unit or "").strip().rstrip(".").split())
    if not u:
        return None, Decimal(1)
    for pat, base, factor in _UNITS:
        if re.fullmatch(pat, u, re.I):
            return base, factor
    return u.lower(), Decimal(1)


def to_base(qty, unit: str | None) -> tuple[Decimal | None, str | None]:
    q = to_decimal(qty)
    base, factor = norm_unit(unit)
    return (q * factor if q is not None else None), base


_UNIT_RX = (r"(?P<unit>lakh\s*cum|cum\s*/\s*h(?:ou)?r|cum\s+per\s+hour|m3\s*/\s*h(?:ou)?r|TPH|tph|cum\b|cu\.?\s?m\b|m3|m³|sqm|sq\.?\s?m\b|m2\b|"
            r"MT\b|M\.T\.?|metric\s+tonnes?|tonnes?|kms?\b|RMT|Rmt|rmt|running\s+met(?:er|re)s?|met(?:er|re)s?\b|mtrs?\b|KVA|kVA|kW\b|KW\b|HP\b|"
            r"KL\b|kl\b|litres?|ltrs?|MLD)")
_RATE_UNITS = ("cum/hr", "TPH", "kVA", "kW", "HP", "mm", "MLD")

# ------------------------------------------------------------------ work items (physical quantities)
WORK_ITEMS: dict[str, str] = {
    "earthwork": r"earth\s*work|excavation|earth\s+filling|embankment",
    "rcc": r"\bR\.?C\.?C\.?(?!\s+pipe)\b|reinforced\s+(?:cement\s+)?concrete",
    "reinforcement_steel": r"reinforcement|\bTMT\b|\bHYSD\b|steel\s+bars?",
    "structural_steel": r"structural\s+steel|steel\s+structur|fabricat",
    "pipe_laying": r"pipe(?:line)?s?\b|pipe\s+laying",
    "concrete": r"concrete|\bP\.?C\.?C\.?\b",
    "bituminous": r"bitumin|asphalt|\bDBM\b|\bSDBC\b|premix",
    "granular": r"\bWMM\b|wet\s+mix|\bGSB\b|granular|\bWBM\b",
    "brickwork": r"brick\s*work|masonry|bricks?",
    "piling": r"\bpil(?:e|es|ing)\b",
    "plastering": r"plaster",
    "flooring": r"flooring|tiling|tiles",
}
PIPE_MATERIALS = {"DI": r"\bD\.?I\.?\b|ductile", "MS": r"\bM\.?S\.?\b|mild\s+steel", "HDPE": r"HDPE",
                  "PVC": r"\bu?PVC\b", "CI": r"\bC\.?I\.?\b|cast\s+iron", "GRP": r"\bGRP\b|\bGRE\b"}


def canonical_item(text: str | None) -> str | None:
    text = text or ""
    if re.search(r"concrete[^,;]{0,30}includ", text, re.I):
        return "concrete"  # "cement concrete including RCC" is all concrete
    for key, pat in WORK_ITEMS.items():
        if re.search(pat, text, re.I):
            return key
    return None


def pipe_material(text: str | None) -> str | None:
    for k, pat in PIPE_MATERIALS.items():
        if re.search(pat, text or "", re.I):
            return k
    return None


def item_matches(required: str, offered: str, required_material: str | None = None) -> bool:
    a, b = canonical_item(required), canonical_item(offered)
    if a == "concrete" and b == "rcc":
        ok = True  # reinforced concrete is concrete; plain concrete is not RCC
    elif a or b:
        ok = a == b
    else:
        ra, ob = _tokens(required), _tokens(offered)
        ok = bool(ra) and ra <= ob
    if ok and required_material:
        ok = pipe_material(offered) == required_material
    return ok


_DIA_RX = re.compile(r"(\d{2,4})\s*mm\s*(?:dia(?:meter)?|ø|NB|DN)|(?:dia(?:meter)?|DN|NB)\.?\s*(?:of\s*)?(\d{2,4})\s*mm", re.I)

# ------------------------------------------------------------------ registration class
REG_CLASS_RE = re.compile(
    r"registered\s+(?:contractors?\s+|firms?\s+)?(?:with\s+(?P<auth1>[A-Z][A-Za-z.&/() ]{1,50}?)\s+)?(?:in|of|under|as)\s+(?:the\s+)?"
    r"(?:appropriate\s+|minimum\s+|requisite\s+)?class[\s-]*(?P<cls>[IVX]{1,4}(?:\s*\(\s*[A-C]\s*\)|-[A-C]\b)?(?![A-Za-z])|[A-E]{1,2}\b|\d\b|Super\b|Special\b)"
    r"(?P<above>\s*(?:or|and)\s+above)?(?:[^.\n]{0,20}?\b(?:with|in|of|under)\s+(?P<auth2>[A-Z][A-Za-z.&/() ]{1,50}))?", re.I)


def norm_class(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"class", "", (s or "").lower()))


# ------------------------------------------------------------------ section parsing
class _Header:
    """A section-heading matcher. `strict` matches anywhere on a line; `heading` matches only on a short,
    heading-like line, so a sentence such as 'confirming the availability of the machinery and equipment
    required' is not taken as the start of an equipment list."""

    def __init__(self, strict: str, heading: str | None = None):
        self.strict = re.compile(strict, re.I)
        self.heading = re.compile(heading, re.I) if heading else None

    def search(self, line: str):
        m = self.strict.search(line)
        if m or not self.heading:
            return m
        body = _MARKER.sub("", line, count=1).strip()
        body = re.sub(r"^\(?[A-H]\)\s*", "", body)
        if len(body) <= 70:
            return self.heading.search(line)
        return None


PERSONNEL_HDR = _Header(r"(?:key|technical|essential|minimum)\s+(?:technical\s+)?(?:personnel|staff|man\s*power)|"
                        r"personnel\s+(?:requirement|criteria)|deploy(?:ment)?\s+(?:of\s+)?(?:the\s+following\s+)?(?:technical\s+)?(?:staff|personnel)",
                        r"personnel\s+capabilit|technical\s+man\s*power|qualified\s+personnel")
EQUIP_HDR = _Header(r"plants?\s*(?:&|and)\s*(?:machinery|equipment)|"
                    r"(?:key|essential|minimum|major)\s+(?:construction\s+)?(?:equipment|machinery)|equipment\s+(?:requirement|to\s+be\s+(?:owned|deployed))",
                    r"plants?\s*[,/]\s*(?:machinery|equipment)|machinery\s*(?:,|&|and)\s*(?:equipment|tools)|tools\s*(?:,|&|and)\s*(?:plants?|machinery|equipment)|"
                    r"^\W*(?:[A-H][).]\s*)?(?:list\s+of\s+)?(?:machinery|equipment)\s*:?\s*$")
QTY_HDR = re.compile(r"physical\s+(?:qualification|performance|quantit)|minimum\s+quantit|following\s+(?:minimum\s+)?quantities|"
                     r"quantities\s+of\s+(?:the\s+following\s+)?(?:key\s+)?(?:items|works?)", re.I)
_ANY_HDR = [PERSONNEL_HDR, EQUIP_HDR, QTY_HDR]
_HEADING_LINE = re.compile(r"^\s*\d{1,2}\.?\s+[A-Z][A-Z &/,\-()]{4,}$")  # "12. DOCUMENTS TO BE UPLOADED"
_TOP_CLAUSE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\s+[A-Z]")  # "10.7 The bidder ..." ends a list that sits inside 10.6
# A bare leading number ("4 Nos. transit mixers") is a count, not a list marker: numbers need "." or ")".
_MARKER = re.compile(r"^\s*(?:\(?[ivxIVX]{1,4}\)|\(?[a-zA-Z]\)|(?:[ivxIVX]{1,4}|[a-z])\.(?=\s)|\d{1,2}(?:\.\d{1,2}){0,3}[.)]|[•\-–*▪●◦])(?:\s+|$)")
_MARKER_ONLY = re.compile(r"^\s*(?:\(?[ivxIVX]{1,4}[.)]|\(?[a-zA-Z][.)]|\d{1,2}[.)]|[•\-–*▪●◦])\s*$")
_ROLE_NOUN = re.compile(r"engineer|manager|officer|supervisor|chemist|expert|surveyor|in[\s-]?charge|foreman|operator|specialist|"
                        r"\bhead\b|leader|planner|accountant|electrician|geologist|mechanic|inspector|co-?ordinator|director|"
                        r"architect|hydrologist|technician|draughtsman|draftsman|consultant", re.I)
_COUNT_RX = re.compile(r"(?<![\d.])(" + NUMW + r")\s*(?:\(\s*\d+\s*\)\s*)?(?:Nos?\b\.?|numbers?\b|persons?\b|units?\b|sets?\b)", re.I)
_LEAD_COUNT = re.compile(r"^(" + NUMW + r")\s*(?:\(\s*\d+\s*\)\s*)?(?:Nos?\.?|numbers?|units?|sets?)?\s+(?=[A-Za-z])", re.I)
_EXP_RX = re.compile(r"(\d{1,2})\s*(?:\+\s*)?(?:years?|yrs?)\b", re.I)
_CAP_RX = re.compile(r"(\d+(?:\.\d+)?)\s*(cum\s*/\s*h(?:ou)?r|cum\s+per\s+hour|m3\s*/\s*h(?:ou)?r|cu\.?\s*m\s*/\s*h(?:ou)?r|TPH|tph|"
                     r"tonnes?\s*/\s*h(?:ou)?r|KVA|kVA|kva|kW|KW|HP|hp|MT|tonnes?|tons?|cum|m3|KL|kl|litres?|ltrs?|mm)(?![A-Za-z])", re.I)
# A list ends at a lettered heading ("C) Plant, Machinery ...") or at the JV / notes paragraph under it.
_SECTION_STOP = re.compile(r"^\s*(?:\(?[A-H]\)\s+[A-Z]|(?i:in\s+case\s+of\s+(?:a\s+)?(?:joint|JV|consortium)|notes?\s*:?\s*$))")


# ---- tables that continue on the next page
_PAGE_NOISE = re.compile(r"^\s*(?:page\s*(?:no\.?\s*)?\d+(?:\s*(?:of|/)\s*\d+)?|-\s*\d+\s*-|\d{1,4}|\(\s*\d+\s*\)|"
                         r"\(?contd\.?\)?|continued|\.{3,}|cont(?:inued)?\s+(?:on|from)\s+(?:next|previous)\s+page)\s*$", re.I)
_COL_HEADER = re.compile(r"^\s*(?:s\.?\s*no\.?|sl\.?\s*no\.?|sr\.?\s*no\.?|serial\s+no\.?|item\s+no\.?)(?=\s|$)|"
                         r"^\s*(?:designation|description|name\s+of\s+(?:post|equipment|item)|post|position|type\s+of\s+equipment)"
                         r"\s+(?:minimum\s+)?(?:qualification|capacity|nos?\.?|number|quantity|experience)", re.I)


def _is_stop(line: str) -> bool:
    return bool(any(h.search(line) for h in _ANY_HDR) or _HEADING_LINE.match(line) or _TOP_CLAUSE.match(line)
                or _SECTION_STOP.match(line))


def open_section_at_end(text: str) -> tuple[str, str | None] | None:
    """If a personnel / machinery / quantity list is still running at the bottom of the page, return
    (its heading line, its last item line). None when every list on the page is closed."""
    lines = [ln for ln in text.replace("\r", "").split("\n")]
    while lines and (not lines[-1].strip() or _PAGE_NOISE.match(lines[-1])):
        lines.pop()  # footer: page numbers, "contd."
    best: tuple[int, str] | None = None
    for hdr in _ANY_HDR:
        for i, ln in enumerate(lines):
            if hdr.search(ln) and (best is None or i > best[0]):
                best = (i, ln)
    if best is None:
        return None
    i, head = best
    last = None
    for ln in lines[i + 1:]:
        if not ln.strip() or _PAGE_NOISE.match(ln) or _COL_HEADER.match(ln):
            continue
        if _is_stop(ln):
            return None
        last = ln
    return head, last


def leading_rows(text: str, limit: int = 30) -> list[str]:
    """Rows at the top of a page that continue a list from the previous page (stops at the next heading)."""
    out: list[str] = []
    blanks = 0
    for ln in text.replace("\r", "").split("\n")[:limit + 15]:
        if not ln.strip():
            if out:
                blanks += 1
                if blanks >= 2:
                    break
            continue
        if _PAGE_NOISE.match(ln) or _COL_HEADER.match(ln):
            continue  # running header, page number or the table's repeated column headings
        if _is_stop(ln):
            break
        out.append(ln)
        blanks = 0
        if len(out) >= limit:
            break
    return out


def is_fragment(line: str) -> bool:
    """A line that finishes a row cut by the page break: no list marker and starts in lower case or with 'with'/'and'."""
    body = line.strip()
    return bool(body) and not _MARKER.match(line) and (body[0].islower() or bool(re.match(r"(?:with|and|having|of)\b", body, re.I)))


def _strip_marker(line: str) -> str:
    return _MARKER.sub("", line, count=1).strip()


def _section_lines(lines: list[str], hdr: re.Pattern) -> list[tuple[int, str, list[tuple[int, str]]]]:
    """For every header line on the page: (header index, header text, following item lines)."""
    blocks = []
    consumed: set[int] = set()
    for i, ln in enumerate(lines):
        hm = hdr.search(ln)
        if not hm or i in consumed:
            continue
        items: list[tuple[int, str]] = []
        tail = ln[hm.end():]
        if ":" in tail and tail.split(":", 1)[1].strip():
            items.append((i, tail.split(":", 1)[1].strip()))
        blanks = 0
        for j in range(i + 1, min(len(lines), i + 20)):
            nxt = lines[j]
            if not nxt.strip():
                blanks += 1
                if blanks >= 2 and items:
                    break
                continue
            if (any(h.search(nxt) for h in _ANY_HDR if h is not hdr) or _HEADING_LINE.match(nxt) or _TOP_CLAUSE.match(nxt)
                    or _SECTION_STOP.match(nxt)):
                break
            items.append((j, nxt))
        consumed.update(j for j, _ in items)
        blocks.append((i, ln, items))
    return blocks


def _join_wrapped(items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join continuation lines (no list marker, starting lower-case) to the previous item."""
    out: list[tuple[int, str]] = []
    pending_marker: str | None = None
    for idx, ln in items:
        s = ln.strip()
        if _MARKER_ONLY.match(ln):  # "i." or "•" on its own line: the item text is on the next line
            pending_marker = s
            continue
        if pending_marker is not None:
            out.append((idx, f"{pending_marker} {s}"))
            pending_marker = None
        elif out and not _MARKER.match(ln) and (s[:1].islower() or s[:1] in "(&,"):
            out[-1] = (out[-1][0], out[-1][1] + " " + s)
        else:
            out.append((idx, s))
    return out


def _join_sentences(items: list[tuple[int, str]]) -> list[tuple[int, str, bool]]:
    """Like _join_wrapped, but a line also continues the previous one when that one did not end a sentence
    ('... of height not less than' / '5 m and length ...'). Returns (line index, text, had a list marker)."""
    out: list[tuple[int, str, bool]] = []
    pending = False
    for idx, ln in items:
        s = ln.strip()
        if not s:
            continue
        if _MARKER_ONLY.match(ln):
            pending = True
            continue
        marked = pending or bool(_MARKER.match(ln))
        if pending:
            out.append((idx, s, True))
            pending = False
        elif out and not marked and not re.search(r"[.:;]\s*$", out[-1][1]):
            out[-1] = (out[-1][0], out[-1][1] + " " + s, out[-1][2])
        else:
            out.append((idx, _strip_marker(s) if marked else s, marked))
    return out


_QUAL_START = r"(?:B\.?\s?E\b|B\.?\s?Tech|Diploma|Graduate|Post[\s-]?Graduate|M\.?\s?Tech|M\.\s?E\b|I\.?T\.?I\b|Degree)"


def parse_personnel_line(line: str) -> dict | None:
    s = _strip_marker(line)
    lead = _LEAD_COUNT.match(s)
    body = s[lead.end():] if lead else s
    m = re.match(r"(?P<role>[A-Za-z][A-Za-z /&.\-]{2,60}?)\s*(?=[:–,(]|\s-\s|\s+with\b|\s+having\b|\s+\d|\s+" + _QUAL_START + r"|$)", body, re.I)
    role = (m.group("role") if m else "").strip(" .-–")
    if not role or not _ROLE_NOUN.search(role):
        return None
    count = _wn(lead.group(1)) if lead else None
    if (cm := _COUNT_RX.search(s)):
        count = _wn(cm.group(1))
    role = re.sub(r"\b(engineer|manager|officer|supervisor|chemist|expert|surveyor|foreman|operator|technician)s\b", r"\1", role, flags=re.I)
    rest = body[len(m.group("role")):] if m else body
    em = _EXP_RX.search(rest)
    qual = min_qualification(body)  # "Graduate Civil Engineer" carries the qualification in the role itself
    return {"role": " ".join(role.split()), "count": count, "min_qualification": qual,
            "disciplines": disciplines(body) if qual else [],
            "min_experience_years": int(em.group(1)) if em else None, "line": s}


def parse_equipment_line(line: str) -> dict | None:
    s = _strip_marker(line)
    if not _EQUIP_NOUN.search(s):
        return None
    lead = _LEAD_COUNT.match(s)
    body = s[lead.end():] if lead else s
    count = _wn(lead.group(1)) if lead else None
    if (cm := _COUNT_RX.search(s)):
        count = _wn(cm.group(1))
    cap = _CAP_RX.search(body)
    name = re.split(r"\s*(?:[:–(]|\s-\s|,|\s+of\s+(?:capacity|min|required|adequate|suitable|appropriate)|\s+capacity|\s+having|"
                    r"\s+with\b|\s+capable\b|\s+suitable\b|\s+for\s+(?!the\s+)|\s+as\s+per\b|\s+\d)", body, maxsplit=1)[0]
    name = re.sub(r"\s+(?:of|with|having|capacity)$", "", " ".join(name.split()), flags=re.I).strip(" .-–")
    if not name or not _EQUIP_NOUN.search(name) or _EQUIP_SENTENCE.search(name) or len(name) > 70:
        return None
    spec = " ".join(body[len(name):].split()).strip(" ,.-–:") if body.lower().startswith(name.lower()) else ""
    return {"equipment": name, "canonical": canonical_equipment(name), "count": count,
            "min_capacity": cap.group(1) if cap else None, "capacity_unit": norm_unit(cap.group(2))[0] if cap else None,
            "specification": spec or None, "line": s}


def parse_equipment_items(line: str) -> list[dict]:
    """One list line can name several machines ('Transit Mixers and Concrete Pumps capable of ...')."""
    e = parse_equipment_line(line)
    if not e:
        return []
    parts = [p.strip() for p in re.split(r"\s+(?:and|&)\s+|\s*[,/]\s*", e["equipment"]) if p.strip()]
    kinds = [canonical_equipment(p) for p in parts]
    if len(parts) < 2 or not all(kinds) or len(set(kinds)) < len(kinds):
        return [e]
    return [{**e, "equipment": p, "canonical": k} for p, k in zip(parts, kinds)]


_QTY_LINE = re.compile(r"(?P<item>[A-Za-z(][A-Za-z0-9 ,./&()'\-]{2,90}?)\s*(?:[:=–]|\s-\s|\s)\s*(?:of\s+)?(?:not\s+less\s+than\s+|minimum\s+|min\.?\s+|at\s+least\s+)?"
                       r"(?P<qty>\d[\d,]*(?:\.\d+)?)\s*" + _UNIT_RX, re.I)


def parse_quantity_line(line: str) -> dict | None:
    s = _strip_marker(line)
    m = _QTY_LINE.search(s)
    if not m:
        return None
    item = re.sub(r"\(?\s*\d{2,4}\s*mm\s*(?:dia(?:meter)?)?(?:\s+and\s+above)?\s*\)?", "", m.group("item"), flags=re.I)
    item = re.sub(r"(?:\b(?:of|total(?:\s+length)?|quantity|having|minimum|not\s+less\s+than|min)\b\.?\s*)+$", "", item.strip(), flags=re.I)
    item = " ".join(item.split()).strip(" .,:-–(")
    qty = to_decimal(m.group("qty"))
    if qty is None or qty == 0 or not item:
        return None
    base = norm_unit(m.group("unit"))[0]
    if base in _RATE_UNITS:
        return None  # a capacity, not an executed quantity
    dia = _DIA_RX.search(s)
    canon = canonical_item(item)
    return {"item": item, "canonical": canon, "min_quantity": str(qty), "unit": " ".join(m.group("unit").split()),
            "min_dia_mm": int(dia.group(1) or dia.group(2)) if dia else None,
            "material": pipe_material(s) if canon == "pipe_laying" else None, "line": s}


def _basis(text: str) -> str | None:
    if re.search(r"any\s+one\s+(?:financial\s+)?year|in\s+a\s+(?:single\s+)?(?:financial\s+)?year|(?:12|twelve)\s+(?:consecutive\s+)?months", text, re.I):
        return "any_one_year"
    if re.search(r"cumulative|in\s+total|aggregate|during\s+the\s+last|in\s+the\s+last", text, re.I):
        return "cumulative"
    return None


def _period(text: str) -> int | None:
    m = re.search(r"(?:last|preceding|previous)\s+(" + NUMW + r")\s+(?:\(\d+\)\s+)?(?:financial\s+|completed\s+)?years?", text, re.I)
    return _wn(m.group(1)) if m else None


def _ownership(text: str) -> str:
    owned = re.search(r"\bown(?:ed|ership)?\b", text, re.I)
    leased = re.search(r"leas|hire|rent|arrange", text, re.I)
    return "owned" if owned and not leased else "owned_or_leased"


# ------------------------------------------------------------------ dimensions & nature of work (experience criteria)
_LEN_UNITS = {"mm": Decimal("0.001"), "cm": Decimal("0.01"), "m": Decimal(1), "mtr": Decimal(1), "mtrs": Decimal(1),
              "metre": Decimal(1), "metres": Decimal(1), "meter": Decimal(1), "meters": Decimal(1), "rmt": Decimal(1),
              "km": Decimal(1000), "kms": Decimal(1000)}
_DIM_WORDS = {"height": "height", "high": "height", "tall": "height", "h": "height", "length": "length", "long": "length",
              "l": "length", "depth": "depth", "deep": "depth", "d": "depth", "width": "width", "wide": "width", "w": "width",
              "span": "span", "diameter": "diameter", "dia": "diameter", "thickness": "thickness", "thick": "thickness"}
_DIM_UNIT_RX = r"(?P<unit>mm|cm|km|kms|rmt|mtrs?|metres?|meters?|m)(?![a-z0-9²³])"
_DIM_NAME_FIRST = re.compile(
    r"\b(?P<name>height|length|depth|width|span|diameter|dia|thickness|H|L|D|W)\b\.?\s*(?:of\s+)?(?:the\s+\w+\s+)?"
    r"(?:(?:shall\s+be\s+|should\s+be\s+)?(?:not\s+less\s+than|minimum(?:\s+of)?|min\.?|at\s+least|more\s+than|exceeding|over|above|upto|up\s+to)\s*|[=:≥>]=?\s*)?"
    r"(?P<val>\d+(?:\.\d+)?)\s*" + _DIM_UNIT_RX, re.I)
_DIM_VALUE_FIRST = re.compile(r"(?<![\d.])(?P<val>\d+(?:\.\d+)?)\s*" + _DIM_UNIT_RX + r"\s+(?:in\s+)?(?P<name>high|tall|long|deep|wide|thick|height|length|depth|width|span|dia(?:meter)?)\b", re.I)


def parse_dimensions(text: str | None) -> list[dict]:
    """Minimum sizes printed in a criterion: 'height not less than 7.5 m and length not less than 10 m'
    -> [{'name': 'height', 'min': '7.5', 'unit': 'm'}, {'name': 'length', 'min': '10', 'unit': 'm'}] (metres)."""
    found: dict[str, Decimal] = {}
    for rx in (_DIM_NAME_FIRST, _DIM_VALUE_FIRST):
        for m in rx.finditer(text or ""):
            name = _DIM_WORDS.get(m.group("name").lower())
            factor = _LEN_UNITS.get(m.group("unit").lower())
            if not name or factor is None or (len(m.group("name")) == 1 and m.group("name").islower()):
                continue
            val = Decimal(m.group("val")) * factor
            found.setdefault(name, val)
    return [{"name": n, "min": format(v.normalize(), "f"), "unit": "m"} for n, v in found.items()]


def dimensions_on_record(text: str | None) -> dict[str, Decimal]:
    """Sizes stated on a completion certificate / in the vault record ('8 m high, 120 m long' or 'H = 8 m')."""
    out: dict[str, Decimal] = {}
    for d in parse_dimensions(text):
        out[d["name"]] = max(out.get(d["name"], Decimal(0)), Decimal(d["min"]))
    return out


_NATURE_STOP = {"construction", "constructing", "execution", "executing", "executed", "work", "works", "of", "and", "or", "the",
                "with", "in", "for", "on", "at", "to", "a", "an", "including", "related", "allied", "type", "nature", "similar",
                "project", "projects", "successful", "successfully", "completed", "completion", "experience", "having",
                "providing", "satisfactory", "any", "other", "etc", "such", "as", "by", "from", "its", "their", "all"}


def nature_core(text: str | None) -> str:
    """'construction of retaining wall of height not less than 7.5 m ...' -> 'construction of retaining wall'."""
    t = " ".join((text or "").split())
    t = re.split(r"\s+(?:of|with|having|having\s+a)\s+(?:a\s+)?(?:minimum\s+)?(?:height|length|depth|width|span|dia(?:meter)?|thickness)\b|"
                 r"\s+(?:not\s+less\s+than|minimum|at\s+least)\s+\d|\s*,?\s*(?:during|within|in\s+the\s+(?:last|preceding|past))\b|"
                 r"\s*\(|\s*[.;]\s", t, maxsplit=1, flags=re.I)[0]
    return t.strip(" ,.-–:")


def nature_keywords(text: str | None) -> list[str]:
    """Salient words of the work description. 'RCC/PCC' stays one alternative group: 'rcc/pcc'."""
    out: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:/[A-Za-z][A-Za-z0-9]*)*", nature_core(text)):
        alts = [a.lower() for a in tok.split("/") if a.lower() not in _NATURE_STOP and len(a) > 1]
        if not alts:
            continue
        alts = [a[:-1] if len(a) > 4 and a.endswith("s") and not a.endswith("ss") else a for a in alts]
        k = "/".join(alts)
        if k not in out:
            out.append(k)
    return out[:8]


def keywords_match(keywords: list[str] | None, text: str | None) -> tuple[bool, list[str]]:
    """Does a work description mention the tender's kind of work? Returns (matches, missing keyword groups).
    A heuristic hint for the reviewer only: similarity is always confirmed by a person."""
    if not keywords:
        return True, []
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    missing = [k for k in keywords if not any(w.startswith(alt[:max(4, len(alt) - 2)]) or w == alt
                                              for alt in k.split("/") for w in words)]
    need = -(-len(keywords) * 6 // 10)  # at least 60% of the keyword groups
    return len(keywords) - len(missing) >= need, missing


# ------------------------------------------------------------------ experience criteria (general / specific)
_AMT_T = r"(?:Rs\s?\.{0,2}|INR|₹)\s*[\d,]+(?:\.\d+)?\s*(?:crores?|cr\.?|lakhs?|lacs?)?"
GEN_EXP_HDR = re.compile(r"general\s+(?:construction\s+|work\s+)?experience", re.I)
SPEC_EXP_HDR = re.compile(r"(?:specific|particular)\s+(?:construction\s+|work\s+)?experience", re.I)
_EXP_STOP = re.compile(r"^\s*(?:\(?[A-H]\)\s+[A-Z]|[A-H]\.\s+[A-Z][a-z]|\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\.?\s|\d{1,2}\.\s+[A-Z]{3})")
_ALT_RX = re.compile(
    r"(?<![\d.])(?P<n>" + NUMW + r")\s+(?:\(\s*\d+\s*\)\s+)?(?P<kind>(?:(?:similar|completed|ongoing|on-going|successfully|"
    r"substantially|works?\s+in\s+progress|or|and|/|,)\s*){0,6})(?:projects?|works?|contracts?|jobs?)\b(?P<mid>[^•;]{0,140}?)"
    r"(?:not\s+less\s+than|minimum(?:\s+of)?|at\s+least|costing|of\s+value)\s+(?:(?P<pct>\d{1,3}(?:\.\d+)?)\s*%|(?P<amt>" + _AMT_T + "))", re.I)
_WORK_START = re.compile(r"^(?:construction|execution|laying|installation|supply|design|erection|commissioning|providing|"
                         r"rehabilitation|repair|stabili[sz]ation|protection|work|experience|completion|building|development|"
                         r"improvement|widening|strengthening|maintenance|excavation|tunnel|bridge|road|slope|retaining|drilling|"
                         r"grouting|piling|one|two|three|four|five|at\s+least)", re.I)
_EXP_NOT_ITEM = re.compile(r"^(?:in\s+case|the\s+(?:specific\s+|general\s+)?experience\s+(?:may|shall|claim|should)|notes?\b|"
                           r"experience\s+(?:certificate|of\s+all)|the\s+submitted|the\s+tender|the\s+bidder|the\s+applicant|"
                           r"documentary|certificate)", re.I)
_EXP_NATURE = re.compile(r"experience\s+(?:of|in)\s+(?:having\s+)?(?:successfully\s+)?(?:executing|execution\s+of|executed|"
                         r"completing|completed|carrying\s+out|undertaking)?\s*(?P<n>.{5,240}?)\s*(?:,?\s*during\b|,?\s*within\b|"
                         r",?\s*in\s+the\s+(?:last|preceding|past)\b|,?\s*over\s+the\s+last\b|,?\s*of\s+value\b|\s*\(reckoned|:|$)", re.I)


def _jv_hint(text: str) -> dict | None:
    """The JV note printed under one criterion ('Experience of all JV partners shall be aggregated')."""
    rules = [("aggregate_sum", r"(?:aggregated|added\s+together|combined|clubbed|considered\s+(?:together|jointly|collectively))"),
             ("lead_only", r"lead\s+(?:partner|member)\s+(?:alone|only|itself|by\s+itself)|by\s+the\s+lead\s+(?:partner|member)\s+(?:alone|only)"),
             ("any_member", r"(?:any\s+one|either|any)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:partners?|members?|constituents?)"),
             ("each_member", r"(?:each|every|all)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:partners?|members?)\s+(?:shall|should|must)\s+(?:individually\s+)?(?:meet|have|satisfy)")]
    m0 = re.search(r"(?:joint\s+venture|\bJV\b|consortium)", text, re.I)
    if not m0:
        return None
    tail = text[m0.start():]
    for mode, pat in rules:
        m = re.search(pat, tail, re.I)
        if m:
            s = max(tail.rfind(".", 0, m.start()) + 1, 0)
            e = tail.find(".", m.end())
            sentence = " ".join(tail[s: e if e >= 0 else len(tail)].replace("•", " ").split())
            return {"mode": mode, "text": sentence[:240]}
    return None


def _block(lines: list[str], start: int, max_lines: int = 45) -> list[tuple[int, str]]:
    out = []
    for j in range(start + 1, min(len(lines), start + max_lines)):
        ln = lines[j]
        hm = GEN_EXP_HDR.search(ln) or SPEC_EXP_HDR.search(ln)
        if ln.strip() and ((hm and _is_heading(ln, hm)) or EQUIP_HDR.search(ln) or PERSONNEL_HDR.search(ln)
                           or _EXP_STOP.match(ln) or _HEADING_LINE.match(ln)):
            break
        out.append((j, ln))
    return out


def _is_heading(line: str, m: re.Match) -> bool:
    """'A) General Experience', '1.6.2 Specific Experience:', 'SPECIFIC EXPERIENCE' - not 'The specific experience may ...'."""
    before = _MARKER.sub("", line[: m.start()], count=1)
    before = re.sub(r"^\s*\(?[A-Ha-h][).]\s*|^\s*\d{1,2}(?:\.\d{1,2}){0,3}\.?\s*|^\s*(?:criteri(?:on|a)|minimum|key)\s*", "",
                    before, flags=re.I).strip(" :-–")
    after = line[m.end():].strip()
    return not before and (len(after) <= 40 or after[:1] in ":-–(")


def extract_experience(text: str, page_index: int, page_conf: float | None, clause_for, Candidate) -> tuple[list, list[tuple[int, int]]]:
    """General and specific experience criteria, read section by section. Returns candidates and the text spans
    they cover (so the generic 'N similar works' reader does not read the same sentence twice)."""
    out, spans = [], []
    lines = text.replace("\r", "").split("\n")
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    def conf(base: float) -> float:
        return round(min(base, page_conf if page_conf is not None else base), 3)

    used: set[str] = set()

    def add(key, title, value, excerpt, line_idx, base, notes, mandatory=True):
        k, n = key, 2
        while k in used:
            k, n = f"{key}_{n}", n + 1
        used.add(k)
        out.append(Candidate(k, "experience", title, value, " ".join(excerpt.split())[:600], page_index,
                             clause_for(text, offsets[line_idx]), conf(base), mandatory=mandatory, notes=notes,
                             extractor="technical-v2"))

    for i, ln in enumerate(lines):
        gm, sm = GEN_EXP_HDR.search(ln), SPEC_EXP_HDR.search(ln)
        m = gm or sm
        if not m or not _is_heading(ln, m):
            continue
        body = _block(lines, i)
        if not body:
            continue
        block = " ".join([ln[m.end():]] + [x for _, x in body])
        flat_block = " ".join(block.split())
        end_line = body[-1][0]
        span = (offsets[i], offsets[end_line] + len(lines[end_line]))
        period = _period(flat_block)
        hint = _jv_hint(flat_block)
        proof = re.search(r"substantiated\s+by\s+([^.]{5,160})", flat_block, re.I)
        ongoing = bool(re.search(r"on-?going|in\s+progress", flat_block, re.I))
        substantial = bool(re.search(r"substantially\s+completed", flat_block, re.I))
        common_notes = []
        if period is None:
            common_notes.append("Qualifying period (last N years) not found; confirm.")
        if proof:
            common_notes.append(f"Proof the tender asks for: {proof.group(1).strip()}.")
        if hint:
            common_notes.append(f"JV note under this criterion: “{hint['text']}”")
        if gm:
            nm = _EXP_NATURE.search(flat_block)
            nature = nature_core(nm.group("n")) if nm else ""
            dims = parse_dimensions(nm.group("n") if nm else flat_block.split("•")[0])
            alts = list(_ALT_RX.finditer(flat_block))
            if not alts and not nature:
                continue  # a form heading ("2. General Experience" over an empty table), not a criterion
            spans.append(span)
            for a in alts:
                n = _wn(a.group("n")) or 1
                val = {"count": n, "period_years": period, "allow_substantial_completion": substantial,
                       "allow_ongoing": ongoing or bool(re.search(r"on-?going", a.group("kind") or "", re.I)),
                       "experience_type": "general", "work_nature": nature or None, "keywords": nature_keywords(nature),
                       "dimensions": dims}
                if a.group("pct"):
                    val["percent_of_estimate"] = a.group("pct")
                else:
                    from .money import parse_indian_amount
                    amt = parse_indian_amount(a.group("amt"))
                    val["amount"] = str(amt) if amt is not None else None
                if hint:
                    val["jv_hint"] = hint
                notes = list(common_notes)
                if nature:
                    notes.insert(0, f"Each work must be of the kind: {nature}" + (
                        " with " + ", ".join(f"{d['name']} ≥ {d['min']} m" for d in dims) if dims else "") + ".")
                thr = f"{a.group('pct')}% of the estimated cost" if a.group("pct") else a.group("amt")
                add(f"exp.similar_work.{n}", f"General experience: {n} work(s), each ≥ {thr}", val,
                    f"{lines[i].strip()} … {a.group(0)}", i, 0.85 if period else 0.7, notes)
            if not alts:  # nature of work without a value test (rare): one work of that kind
                add("exp.similar_work.1", "General experience", {
                    "count": 1, "period_years": period, "experience_type": "general", "work_nature": nature,
                    "keywords": nature_keywords(nature), "dimensions": dims, "allow_ongoing": ongoing,
                    "allow_substantial_completion": substantial, **({"jv_hint": hint} if hint else {})},
                    flat_block[:400], i, 0.6, ["No value threshold found; enter it if the clause has one."] + common_notes)
            continue
        # specific experience: one criterion per listed work
        items = [(j, s) for j, s, marked in _join_sentences(body)
                 if (marked or parse_dimensions(s)) and _WORK_START.match(s) and not _EXP_NOT_ITEM.match(s) and len(s) > 15]
        if not items:
            nm = _EXP_NATURE.search(flat_block)
            if nm and parse_dimensions(nm.group("n")):
                items = [(i, nm.group("n"))]
        if not items:
            continue
        spans.append(span)
        for j, s in items:
            s = " ".join(s.split()).rstrip(" .;")
            core = nature_core(s)
            lead = _LEAD_COUNT.match(s) or re.match(r"(" + NUMW + r")\s+(?:similar\s+)?(?:works?|projects?)\s+of\s+", s, re.I)
            val = {"work": core, "keywords": nature_keywords(core), "dimensions": parse_dimensions(s),
                   "count": (_wn(lead.group(1)) if lead else None) or 1, "period_years": period,
                   "allow_ongoing": ongoing, "allow_substantial_completion": substantial, "description": s[:300],
                   "experience_type": "specific"}
            vm = re.search(r"(?:not\s+less\s+than|minimum(?:\s+of)?|at\s+least)\s+(?:(\d{1,3}(?:\.\d+)?)\s*%|(" + _AMT_T + "))", s, re.I)
            if vm and vm.group(1):
                val["percent_of_estimate"] = vm.group(1)
            elif vm and vm.group(2):
                from .money import parse_indian_amount
                amt = parse_indian_amount(vm.group(2))
                val["amount"] = str(amt) if amt is not None else None
            if hint:
                val["jv_hint"] = hint
            notes = list(common_notes)
            if not val["dimensions"] and "percent_of_estimate" not in val and "amount" not in val:
                notes.insert(0, "No size or value limit found in this item; the reviewer confirms the kind of work only.")
            add(f"exp.specific.{slug(core)[:40]}", f"Specific experience: {core}", val,
                f"{lines[i].strip()} … {s}", j, 0.85 if period else 0.7, notes)
    return out, spans


# ------------------------------------------------------------------ personnel written in running text
_ROLE_WORDS = (r"engineers?|managers?|officers?|supervisors?|chemists?|surveyors?|foreman|foremen|geologists?|electricians?|"
               r"mechanics?|technicians?|draughtsm[ae]n|draftsm[ae]n|accountants?|in[\s-]?charge|experts?|specialists?|"
               r"inspectors?|planners?|co-?ordinators?")
_INLINE_PERSONNEL = re.compile(
    r"(?<![\d.])(?P<n>" + NUMW + r")\s*(?:\(\s*\w+\s*\)\s*)?(?:nos?\.?|numbers?|persons?)\s+(?:of\s+)?"
    r"(?P<role>(?:[A-Za-z][A-Za-z.&/\-]*\s+){0,3}?(?:" + _ROLE_WORDS + r"))\b(?P<tail>[^;,.]{0,160})", re.I)


def inline_personnel(flat: str) -> list[tuple[re.Match, dict]]:
    """'... availability of atleast 01 no. Civil Engineer with minimum 05 years of work experience, 01 no. Safety Officer'"""
    out = []
    for m in _INLINE_PERSONNEL.finditer(flat):
        role = " ".join(m.group("role").split())
        role = re.sub(r"^(?:qualified|experienced|full[\s-]time|regular|competent|technical)\s+", "", role, flags=re.I)
        role = re.sub(r"\b(engineer|manager|officer|supervisor|chemist|surveyor|geologist|electrician|technician|expert|specialist)s\b",
                      r"\1", role, flags=re.I)
        role = " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in role.split())
        tail = m.group("tail")
        em = _EXP_RX.search(tail)
        qual = min_qualification(role + " " + tail)
        out.append((m, {"role": role, "count": _wn(m.group("n").lstrip("0") or "0") or 1, "min_qualification": qual,
                        "disciplines": disciplines(role + " " + tail),
                        "min_experience_years": int(em.group(1)) if em else None}))
    return out


# Registration class printed in the NIT table ("Class of Contractor" column ... "Class A and above")
_REG_TABLE_HDR = re.compile(r"class\s+of\s+contr\s*-?\s*actors?|category\s+of\s+contr\s*-?\s*actors?|eligible\s+class", re.I)
_REG_TABLE_VAL = re.compile(r"\bclass\s*-?\s*(?P<cls>[A-E]{1,2}|[IVX]{1,4}(?:\s*\(\s*[A-C]\s*\))?|Super|Special)\s+(?P<above>(?:and|or)\s+above)?", re.I)


# ------------------------------------------------------------------ JV criteria split
_PCT_PHRASE = r"(?:at\s+least|not\s+less\s+than|a\s+minimum\s+of|minimum(?:\s+of)?)\s+(\d{1,3})\s*%"
_VERB = r"(?:shall|should|must|has\s+to)\s+(?:individually\s+|alone\s+)?(?:meet|satisfy|fulfil+|have|possess)"
_JV_LEAD_PCT = re.compile(r"lead\s+(?:member|partner|bidder)[^.]{0,120}?" + _VERB + r"\s+" + _PCT_PHRASE +
                          r"(?:\s+of\s+(?:the\s+|each\s+of\s+the\s+|all\s+(?:the\s+)?)?(?P<what>[a-z ,/&-]{0,80}?)\s*(?:criteria|criterion|requirements?|qualifications?)\b)?", re.I)
_JV_MEMBER_PCT = re.compile(r"(?:each|every|other|remaining|all\s+other)\s+(?:of\s+the\s+other\s+)?(?:JV\s+)?(?:members?|partners?)[^.]{0,40}?" + _VERB + r"\s+" + _PCT_PHRASE, re.I)
_JV_COMBINED = re.compile(r"(?:combined|aggregate|jointly|together|collectively)\b[^.]{0,100}?(?:shall|should|must)\s+(?:meet|satisfy|fulfil+)|"
                          r"(?:shall|should|must)\s+(?:collectively|jointly|together)\s+(?:meet|satisfy|fulfil+)|"
                          r"(?:shall|will)\s+be\s+(?:added|combined|aggregated|summed)", re.I)
_JV_SIM_LEAD = re.compile(r"lead\s+(?:member|partner)[^.]{0,80}?(?:alone|by\s+itself|on\s+its\s+own|individually)[^.]{0,80}?similar\s+works?|"
                          r"similar\s+works?[^.]{0,120}?(?:by|of)\s+(?:the\s+)?lead\s+(?:member|partner)\s+(?:alone|only)", re.I)
_JV_SIM_ANY = re.compile(r"similar\s+works?[^.]{0,120}?(?:any|either)\s+(?:one\s+)?(?:of\s+the\s+)?(?:JV\s+)?(?:members?|partners?)|"
                         r"(?:any|either)\s+(?:one\s+)?(?:of\s+the\s+)?(?:JV\s+)?(?:members?|partners?)[^.]{0,80}?similar\s+works?", re.I)
_JV_EACH_NW = re.compile(r"(?:each|every|all)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:members?|partners?)[^.]{0,80}?net\s*worth|"
                         r"net\s*worth[^.]{0,80}?(?:each|every|all)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:members?|partners?)", re.I)
_JV_EACH_DOC = re.compile(r"(?:each|every|all)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:members?|partners?)[^.]{0,80}?(?:registered|registration|PAN|GST|blacklist)", re.I)
_JV_GEN_AGG = re.compile(r"experience\s+of\s+(?:all|the|both)\s+(?:the\s+)?(?:JV\s+)?(?:partners|members|constituents)[^.]{0,40}?"
                         r"(?:shall|will|may|can)\s+be\s+(?:aggregated|added|combined|clubbed|considered\s+(?:together|jointly|collectively))", re.I)
_JV_SPEC_ANY = re.compile(r"specific\s+(?:construction\s+)?experience[^.]{0,60}?(?:any\s+one|either|any)\s+(?:of\s+the\s+)?(?:JV\s+)?(?:partners?|members?)", re.I)
_JV_SPEC_LEAD = re.compile(r"specific\s+(?:construction\s+)?experience[^.]{0,80}?lead\s+(?:partner|member)\s+(?:alone|only)", re.I)
_JV_CLAUSE_REF = re.compile(r"(?:specified|mentioned|given|stated|stipulated)\s+(?:under|in|at)\s+(?:the\s+)?clause\s+([\d.]+\s*(?:\(\s*[A-Za-z0-9]+\s*\))?)", re.I)


def extract_jv_split(flat: str) -> tuple[dict, list[tuple[int, int]]] | None:
    value: dict = {}
    spans: list[tuple[int, int]] = []
    if (m := _JV_LEAD_PCT.search(flat)):
        value["lead_min_pct"] = m.group(1)
        value["applies_to"] = " ".join((m.group("what") or "").split()) or "all"
        if (cm := _JV_CLAUSE_REF.match(flat, m.end()) or _JV_CLAUSE_REF.search(flat[m.end(): m.end() + 40])):
            value["applies_clause"] = " ".join(cm.group(1).split())
        spans.append(m.span())
    if (m := _JV_MEMBER_PCT.search(flat)):
        value["member_min_pct"] = m.group(1)
        spans.append(m.span())
    if (m := _JV_COMBINED.search(flat)):
        value["combined"] = True
        spans.append(m.span())
    if (m := _JV_SIM_LEAD.search(flat)):
        value["similar_work_mode"] = "lead_only"
        spans.append(m.span())
    elif (m := _JV_SIM_ANY.search(flat)):
        value["similar_work_mode"] = "any_member"
        spans.append(m.span())
    if (m := _JV_EACH_NW.search(flat)):
        value["net_worth_mode"] = "each_member"
        spans.append(m.span())
    if (m := _JV_EACH_DOC.search(flat)):
        value["documents_mode"] = "each_member"
        spans.append(m.span())
    if (m := _JV_GEN_AGG.search(flat)):
        value["general_mode"] = "aggregate_sum"
        spans.append(m.span())
    if (m := _JV_SPEC_LEAD.search(flat)):
        value["specific_mode"] = "lead_only"
        spans.append(m.span())
    elif (m := _JV_SPEC_ANY.search(flat)):
        value["specific_mode"] = "any_member"
        spans.append(m.span())
    if not value:
        return None
    if ("lead_min_pct" in value or "member_min_pct" in value) and "combined" not in value:
        value["combined"] = True  # a percentage of a requirement only makes sense if members' figures are added
    return value, sorted(spans)


# ------------------------------------------------------------------ page extraction
def extract_technical(text: str, flat: str, page_index: int, page_conf: float | None, clause_for, sentence_around, Candidate) -> list:
    """Candidates for technical criteria on one page. Helpers are passed in to avoid a circular import."""
    out = []
    lines = text.replace("\r", "").split("\n")
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    def line_at(off: int) -> int:
        return max((i for i, o in enumerate(offsets) if o <= off), default=0)

    def conf(base: float) -> float:
        return round(min(base, page_conf if page_conf is not None else base), 3)

    used: set[str] = set()

    def add(key, cat, title, value, excerpt, line_idx, base, notes, mandatory=True, parent_idx=None):
        k, n = key, 2
        while k in used:
            k, n = f"{key}_{n}", n + 1
        used.add(k)
        clause = clause_for(text, offsets[line_idx])
        sub = re.match(r"^\s*\(?([ivxIVX]{1,4}|[a-zA-Z])\)\s", lines[line_idx]) if parent_idx is not None else None
        if sub:
            clause = f"{clause_for(text, offsets[parent_idx]) or ''}({sub.group(1)})"
        out.append(Candidate(k, cat, title, value, " ".join(excerpt.split())[:600], page_index,
                             clause, conf(base), mandatory=mandatory, notes=notes, extractor="technical-v2"))

    # key personnel
    inline = inline_personnel(flat)
    for hi, hdr, items in _section_lines(lines, PERSONNEL_HDR):
        for idx, ln in _join_wrapped(items):
            if any(m.start() - 40 <= offsets[idx] <= m.end() for m, _ in inline):
                continue  # a wrapped line inside a running-text sentence; read whole by the inline reader below
            p = parse_personnel_line(ln)
            if not p:
                continue
            notes, base = [], 0.85
            if p["count"] is None:
                p["count"] = 1
                notes.append("Number of persons not printed on the line; 1 assumed. Confirm.")
                base = 0.7
            if p["min_qualification"] is None:
                notes.append("Minimum qualification not found; enter it from the source.")
                base = 0.65
            if p["min_experience_years"] is None:
                notes.append("Years of experience not found; enter it from the source (0 if none required).")
                base = min(base, 0.7)
            value = {k: p[k] for k in ("role", "count", "min_qualification", "disciplines", "min_experience_years")}
            add(f"tech.personnel.{slug(p['role'])}", "technical", f"Key personnel: {p['role']}", value,
                f"{hdr.strip()} … {p['line']}", idx, base, notes, parent_idx=hi)
    # plant & machinery
    for hi, hdr, items in _section_lines(lines, EQUIP_HDR):
        ownership = _ownership(" ".join([hdr] + [x for _, x in items]))
        for idx, ln in _join_wrapped(items):
            for e in parse_equipment_items(ln):
                notes, base = [], 0.85
                if e["count"] is None:
                    e["count"] = 1
                    notes.append("Number required not printed on the line; 1 assumed. Confirm.")
                    base = 0.7
                if e["canonical"] is None:
                    notes.append("Equipment type not in the standard list; matched by name words.")
                    base = min(base, 0.75)
                if e["min_capacity"] is None and re.search(r"required|adequate|suitable|appropriate|sufficient", e.get("specification") or "", re.I):
                    notes.append(f"Capacity not stated in figures ('{e['specification']}'); enter it if the department specifies one.")
                value = {"equipment": e["equipment"], "count": e["count"], "min_capacity": e["min_capacity"],
                         "capacity_unit": e["capacity_unit"], "ownership": ownership}
                if e.get("specification"):
                    value["specification"] = e["specification"][:200]
                add(f"tech.equipment.{e['canonical'] or slug(e['equipment'])}", "technical", f"Plant & machinery: {e['equipment']}",
                    value, f"{hdr.strip()} … {e['line']}", idx, base, notes, parent_idx=hi)
    # key personnel written in running text ("at least 01 no. Civil Engineer with minimum 05 years ...")
    have_roles = {c.key.split("tech.personnel.", 1)[1] for c in out if c.key.startswith("tech.personnel.")}
    for m, p in inline:
        if slug(p["role"]) in have_roles:
            continue
        have_roles.add(slug(p["role"]))
        notes, base = ["Read from running text; confirm the post, number and requirement."], 0.75
        if p["min_qualification"] is None and p["min_experience_years"] is None:
            p["min_experience_years"] = 0
            notes.append("The clause names the post but no qualification or years of experience; 0 years assumed. Confirm.")
            base = 0.65
        elif p["min_qualification"] is None:
            notes.append("No minimum degree/diploma printed for this post; only the experience is checked.")
        add(f"tech.personnel.{slug(p['role'])}", "technical", f"Key personnel: {p['role']}", p,
            sentence_around(flat, m.start(), m.end(), 320), line_at(m.start()), base, notes)
    # physical quantities
    qty_found = False
    for hi, hdr, items in _section_lines(lines, QTY_HDR):
        ctx = " ".join(lines[max(0, hi - 3): hi + 3])
        basis, period = _basis(ctx), _period(ctx)
        for idx, ln in _join_wrapped(items):
            q = parse_quantity_line(ln)
            if not q:
                continue
            qty_found = True
            notes, base = [], 0.85
            if basis is None:
                notes.append("Not stated whether the quantity must be in one year or cumulative; confirm.")
                base = 0.7
            if period is None:
                notes.append("Qualifying period (last N years) not found; confirm.")
                base = min(base, 0.7)
            value = {"item": q["item"], "min_quantity": q["min_quantity"], "unit": q["unit"], "basis": basis,
                     "period_years": period, "min_dia_mm": q["min_dia_mm"], "material": q["material"]}
            add(f"tech.quantity.{q['canonical'] or slug(q['item'])}", "technical", f"Physical quantity: {q['item']}",
                value, f"{hdr.strip()} … {q['line']}", idx, base, notes, parent_idx=hi)
    if not qty_found:
        inline = re.compile(r"(?:executed|laid|constructed|completed|commissioned|carried\s+out)\s+(?P<body>[^.]{3,160}?)"
                            r"(?:not\s+less\s+than|minimum(?:\s+of)?|at\s+least|aggregating(?:\s+to)?)\s+(?P<qty>\d[\d,]*(?:\.\d+)?)\s*" + _UNIT_RX, re.I)
        for m in inline.finditer(flat):
            if norm_unit(m.group("unit"))[0] in _RATE_UNITS:
                continue
            body = m.group("body")
            if re.search(r"similar\s+works?|turnover|value", body, re.I):
                continue  # a value criterion, not a physical quantity
            clean = re.sub(r"(?:of\s+)?\d{2,4}\s*mm\s*(?:dia(?:meter)?)?(?:\s+and\s+above)?|\btotal(?:\s+length)?\b|\bhaving\b|"
                           r"\b(?:in|during)\s+(?:any\s+one|the\s+last)[^,]*|\bof\b|,", " ", body, flags=re.I)
            item = " ".join(clean.split())[:80] or "work"
            excerpt = sentence_around(flat, m.start(), m.end(), 360)
            dia = _DIA_RX.search(body)
            canon = canonical_item(item)
            value = {"item": item, "min_quantity": str(to_decimal(m.group("qty"))), "unit": " ".join(m.group("unit").split()),
                     "basis": _basis(excerpt), "period_years": _period(excerpt),
                     "min_dia_mm": int(dia.group(1) or dia.group(2)) if dia else None,
                     "material": pipe_material(body) if canon == "pipe_laying" else None}
            add(f"tech.quantity.{canon or slug(item)}", "technical", f"Physical quantity: {item}", value, excerpt,
                line_at(m.start()), 0.7, ["Read from running text; confirm the item description and quantity."])
    # registration class
    if (m := REG_CLASS_RE.search(flat)):
        auth = (m.group("auth1") or m.group("auth2") or "").strip(" .,")
        auth = re.split(r"\s+(?:and|or|in|for|shall|should|of)\s+", auth)[0].strip() if auth else None
        cls = " ".join(m.group("cls").split())
        notes = ["'or above': add every higher class the department accepts to 'accepted classes'."] if m.group("above") else []
        add("tech.registration_class", "technical", "Contractor registration class",
            {"class": cls, "accepted_classes": [cls], "or_above": bool(m.group("above")), "authority": auth or None},
            sentence_around(flat, m.start(), m.end(), 300), line_at(m.start()), 0.75 if m.group("above") else 0.85, notes)
    elif (hm := _REG_TABLE_HDR.search(flat)) and (vm := _REG_TABLE_VAL.search(flat, hm.end(), hm.end() + 900)):
        cls = " ".join(vm.group("cls").split()).upper()
        notes = ["Read from the NIT table ('Class of Contractor' column); confirm the class and the registering authority."]
        if vm.group("above"):
            notes.append("'and above': add every higher class the department accepts to 'accepted classes'.")
        add("tech.registration_class", "technical", "Contractor registration class",
            {"class": cls, "accepted_classes": [cls], "or_above": bool(vm.group("above")), "authority": None},
            f"{hm.group(0)} … {vm.group(0)}", line_at(vm.start()), 0.65, notes)
    # definition of similar work
    if (m := re.search(r"similar\s+works?\s+(?:shall\s+)?(?:means?|refers?\s+to|is\s+defined\s+as|shall\s+mean|would\s+mean|implies)\s*[:\-–]?\s*(?P<d>[^.]{5,400})", flat, re.I)):
        add("tech.similar_definition", "technical", "Definition of similar work", {"definition": " ".join(m.group("d").split())},
            sentence_around(flat, m.start(), m.end(), 420), line_at(m.start()), 0.85,
            ["Use this definition when marking each completion certificate as similar or not."], mandatory=False)
    # JV criteria split
    if (js := extract_jv_split(flat)):
        value, spans = js
        excerpt = " … ".join(sentence_around(flat, s, e, 300) for s, e in spans[:3])
        add("jv.criteria_split", "jv", "How JV members meet the criteria", value, excerpt, line_at(spans[0][0]), 0.75,
            ["Confirm how each criterion is shared between JV members; the engine never assumes it."])
    return out
