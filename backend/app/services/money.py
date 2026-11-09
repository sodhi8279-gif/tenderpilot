"""Exact Decimal money helpers, Indian number formatting and amount-in-words."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")

UNIT_WORDS = {
    "crore": Decimal("10000000"), "crores": Decimal("10000000"), "cr": Decimal("10000000"), "cr.": Decimal("10000000"),
    "lakh": Decimal("100000"), "lakhs": Decimal("100000"), "lac": Decimal("100000"), "lacs": Decimal("100000"),
    "million": Decimal("1000000"), "thousand": Decimal("1000"),
}


def D(value) -> Decimal:
    """Parse to Decimal. Floats are rejected to prevent binary rounding leaking in."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    if isinstance(value, float):
        raise TypeError("Use str/int/Decimal for money, not float")
    if value is None or value == "":
        raise InvalidOperation("empty value")
    s = str(value).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    return Decimal(s)


def try_D(value) -> Decimal | None:
    try:
        return D(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


def q(value: Decimal, places: int = 2) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def parse_indian_amount(text: str) -> Decimal | None:
    """'Rs. 12.50 Crore' -> 125000000; '₹ 4,50,000' -> 450000; None if no number."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr\.?|lakhs?|lacs?|million|thousand)?", text, re.I)
    if not m:
        return None
    num = try_D(m.group(1))
    if num is None:
        return None
    unit = (m.group(2) or "").lower()
    return num * UNIT_WORDS.get(unit, Decimal(1))


def format_inr(value, places: int = 2) -> str:
    v = q(D(value), places)
    neg = v < 0
    s = f"{abs(v):.{places}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = f"{whole}.{frac}" if places else whole
    return f"-{out}" if neg else out


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _three(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(_ONES[h] + " Hundred")
    if r:
        parts.append(_two(r))
    return " ".join(parts)


def int_to_indian_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    lakh, n = divmod(n, 100_000)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(int_to_indian_words(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(value, currency: str = "INR") -> str:
    v = q(D(value), 2)
    rupees = int(v)
    paise = int((v - rupees) * 100)
    major, minor = ("Rupees", "Paise") if currency == "INR" else (currency, "cents")
    s = f"{major} {int_to_indian_words(rupees)}"
    if paise:
        s += f" and {_two(paise)} {minor}"
    return s + " Only"
