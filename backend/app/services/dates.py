"""Deadline/date parsing that preserves the original wording and flags ambiguity."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


@dataclass
class ParsedDate:
    raw: str
    value: date | None
    time_value: time | None
    ambiguous: bool
    note: str = ""

    def as_datetime(self, tz: str = "Asia/Kolkata") -> datetime | None:
        if self.value is None:
            return None
        return datetime.combine(self.value, self.time_value or time(0, 0), tzinfo=ZoneInfo(tz))


DATE_RE = re.compile(
    r"(?P<d>\d{1,2})[\s./-]*(?:st|nd|rd|th)?[\s./-]*(?P<m>\d{1,2}|[A-Za-z]{3,9})[\s.,/-]*(?P<y>\d{4}|\d{2})\b")
TIME_RE = re.compile(r"(?P<h>\d{1,2})[:.](?P<mi>\d{2})\s*(?P<ap>[AaPp]\.?\s*[Mm]\.?|hrs|Hrs|HRS)?")


def parse_date(text: str) -> ParsedDate:
    m = DATE_RE.search(text)
    if not m:
        return ParsedDate(text, None, None, True, "No date found")
    d, mo, y = m.group("d"), m.group("m"), m.group("y")
    ambiguous = False
    note = ""
    if mo.isdigit():
        month = int(mo)
        day = int(d)
        # Indian tenders use DD/MM/YYYY. If both <= 12 and differ, record that this convention was applied.
        if day <= 12 and month <= 12 and day != month:
            note = "Numeric date read as DD/MM/YYYY (Indian convention); confirm against the portal."
    else:
        month = MONTHS.get(mo[:3].lower())
        if not month:
            return ParsedDate(text, None, None, True, f"Unknown month '{mo}'")
    year = int(y)
    if year < 100:
        year += 2000
        ambiguous = True
        note = (note + " Two-digit year expanded to 20xx.").strip()
    try:
        value = date(year, month, int(d))
    except ValueError:
        return ParsedDate(text, None, None, True, "Invalid calendar date")
    t = None
    tm = TIME_RE.search(text[m.end():m.end() + 40])
    if tm:
        h, mi = int(tm.group("h")), int(tm.group("mi"))
        ap = (tm.group("ap") or "").lower().replace(".", "").replace(" ", "")
        if ap.startswith("p") and h < 12:
            h += 12
        if ap.startswith("a") and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= mi < 60:
            t = time(h, mi)
    return ParsedDate(text, value, t, ambiguous, note)


def detect_timezone(text: str) -> str | None:
    if re.search(r"\bIST\b|Indian Standard Time", text):
        return "Asia/Kolkata"
    if re.search(r"\bUTC\b|\bGMT\b", text):
        return "UTC"
    return None
