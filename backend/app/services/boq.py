"""BOQ import, rate analysis, quote arithmetic, scenarios and safe original-workbook export."""
from __future__ import annotations

import hashlib
import io
import json
import re
from decimal import Decimal

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

from .money import D, ZERO, amount_in_words, q, try_D

HEADER_KEYS = {
    "code": [r"^s\.?\s*no", r"item\s*(no|code)", r"^sl\.?\s*no", r"^sr\.?\s*no", r"^item$"],
    "desc": [r"description", r"particulars", r"name\s+of\s+item", r"item\s+of\s+work"],
    "qty": [r"^qty", r"quantity", r"^quan"],
    "unit": [r"^unit", r"^uom"],
    "rate": [r"^rate", r"unit\s+rate", r"quoted\s+rate", r"rate\s*\(", r"bid\s+rate"],
    "amount": [r"amount", r"total"],
}
UNIT_ALIASES = {
    "cum": "m3", "cu.m": "m3", "cu m": "m3", "m3": "m3", "cubic metre": "m3", "m³": "m3",
    "sqm": "m2", "sq.m": "m2", "sq m": "m2", "m2": "m2", "m²": "m2", "square metre": "m2",
    "rmt": "m", "rm": "m", "m": "m", "metre": "m", "mtr": "m", "rm.": "m", "km": "km",
    "kg": "kg", "mt": "t", "tonne": "t", "t": "t", "qtl": "quintal", "no": "nos", "nos": "nos", "no.": "nos",
    "each": "nos", "set": "set", "job": "job", "ls": "ls", "l.s.": "ls", "lump sum": "ls", "lot": "lot",
    "kl": "kl", "litre": "l", "ltr": "l", "hp": "hp", "kw": "kw", "kva": "kva", "month": "month", "day": "day",
}
TRADES = [
    ("electrical", r"cable|transformer|panel|switchgear|\bdg\b|diesel generator|earthing|lighting|kva|\bkw\b|feeder|mcc"),
    ("instrumentation", r"scada|plc|flow\s*meter|instrument|sensor|telemetry|transmitter"),
    ("mechanical", r"pump|motor|valve|blower|aerator|gate|screen|crane|hoist|clarifier mechanism|compressor"),
    ("piping", r"\bpipes?\b|\bdi\s*k\d|hdpe|upvc|\bfittings?\b|pipeline"),
    ("testing", r"testing|commissioning|trial\s+run|hydraulic\s+test"),
    ("om", r"operation\s+and\s+maintenance|o\s*&\s*m|maintenance\s+for"),
    ("civil", r"."),
]


class BOQError(ValueError):
    pass


def norm_unit(u: str) -> str:
    return UNIT_ALIASES.get((u or "").strip().lower(), (u or "").strip().lower())


def classify_trade(desc: str) -> str:
    d = desc.lower()
    for trade, pat in TRADES:
        if re.search(pat, d):
            return trade
    return "civil"


def inspect_workbook(data: bytes, filename: str) -> dict:
    flags = {"macros": False, "protected_sheets": [], "external_links": False, "drawings": [], "format": "xlsx"}
    issues = []
    if data[:4] == b"\xd0\xcf\x11\xe0":
        return {"flags": {**flags, "format": "xls"}, "issues": ["Legacy .xls cannot be round-tripped safely; manual entry task created."],
                "supported": False}
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
    if "xl/vbaProject.bin" in names:
        flags["macros"] = True
        flags["format"] = "xlsm"
        issues.append("Workbook contains macros. Macros are never stripped: complete bid entry manually in Excel.")
    if any(n.startswith("xl/externalLinks/") for n in names):
        flags["external_links"] = True
        issues.append("Workbook has external links; cannot guarantee safe round-trip.")
    if any(n.startswith(("xl/charts/", "xl/drawings/", "xl/pivotTables/", "xl/slicers/")) for n in names):
        flags["drawings"] = [n for n in names if n.startswith(("xl/charts/", "xl/drawings/", "xl/pivotTables/"))][:10]
        issues.append("Workbook has charts/drawings/pivots that openpyxl may not preserve; export disabled (fail closed).")
    wb = openpyxl.load_workbook(io.BytesIO(data), keep_vba=False)
    for ws in wb.worksheets:
        if ws.protection.sheet:
            flags["protected_sheets"].append(ws.title)
    supported = not (flags["macros"] or flags["external_links"] or flags["drawings"])
    return {"flags": flags, "issues": issues, "supported": supported}


def _match(text: str, keys: list[str]) -> bool:
    t = text.strip().lower()
    return any(re.search(k, t) for k in keys)


def detect_layout(ws) -> tuple[int, dict] | None:
    for r in range(1, min(ws.max_row, 40) + 1):
        cols: dict[str, str] = {}
        for c in range(1, min(ws.max_column, 30) + 1):
            v = ws.cell(r, c).value
            if not isinstance(v, str):
                continue
            for k, pats in HEADER_KEYS.items():
                if k not in cols and _match(v, pats):
                    # "amount" header must not be grabbed by rate columns like "Rate (Amount)"
                    if k == "amount" and "rate" in cols and cols["rate"] == get_column_letter(c):
                        continue
                    cols[k] = get_column_letter(c)
                    break
        if {"desc", "qty", "rate"} <= cols.keys():
            return r, cols
    return None


def parse_boq(data: bytes, filename: str) -> dict:
    info = inspect_workbook(data, filename)
    if info["flags"]["format"] == "xls":
        raise BOQError(info["issues"][0])
    wb = openpyxl.load_workbook(io.BytesIO(data), keep_vba=False)
    for ws in wb.worksheets:
        layout = detect_layout(ws)
        if not layout:
            continue
        header_row, cols = layout
        protected = ws.protection.sheet
        items = []
        seen: dict[str, int] = {}
        desc_units: dict[str, set] = {}
        for r in range(header_row + 1, ws.max_row + 1):
            desc = ws[f"{cols['desc']}{r}"].value
            qty_cell = ws[f"{cols['qty']}{r}"]
            qty = qty_cell.value
            if desc is None or qty is None or isinstance(qty, str) and not try_D(qty):
                continue
            if isinstance(qty, float):
                qty = repr(qty)  # exact text of the stored double, then Decimal
            qd = try_D(qty)
            if qd is None:
                continue
            rate_ref = f"{cols['rate']}{r}"
            rate_cell = ws[rate_ref]
            editable = not (protected and rate_cell.protection.locked)
            if isinstance(rate_cell.value, str) and rate_cell.value.startswith("="):
                editable = False
            unit = str(ws[f"{cols['unit']}{r}"].value or "") if "unit" in cols else ""
            code = str(ws[f"{cols['code']}{r}"].value or "") if "code" in cols else ""
            flags = []
            nkey = re.sub(r"\W+", " ", str(desc).lower()).strip()
            if nkey in seen:
                flags.append(f"duplicate of row {seen[nkey]}")
            seen.setdefault(nkey, r)
            nu = norm_unit(unit)
            desc_units.setdefault(nkey, set()).add(nu)
            if unit and nu not in UNIT_ALIASES.values():
                flags.append(f"unrecognised unit '{unit}'")
            if not unit:
                flags.append("unit missing")
            if not editable:
                flags.append("rate cell locked or formula: manual entry")
            items.append({"row_index": r, "item_code": code, "description": str(desc).strip(), "unit": unit,
                          "quantity": str(qd), "rate_cell": rate_ref,
                          "amount_cell": f"{cols['amount']}{r}" if "amount" in cols else None,
                          "rate_cell_editable": editable, "flags": flags, "trade": classify_trade(str(desc))})
        for it in items:
            nkey = re.sub(r"\W+", " ", it["description"].lower()).strip()
            if len(desc_units.get(nkey, set())) > 1:
                it["flags"].append("same description with inconsistent units")
        if not items:
            continue
        return {"sheet_name": ws.title, "header_row": header_row, "columns": cols, "items": items,
                "roundtrip_supported": info["supported"] and not any(not i["rate_cell_editable"] for i in items),
                "roundtrip_issues": info["issues"] + (["Some rate cells are locked/formulas; those rows need manual entry."]
                                                      if any(not i["rate_cell_editable"] for i in items) else []),
                "flags": info["flags"]}
    raise BOQError("No BOQ table found (need Description, Quantity and Rate columns).")


# ------------------------------------------------------------------ rate analysis
OVERHEAD_KEYS = ["site_overhead", "ho_overhead", "contingency", "risk", "finance_cost", "insurance", "statutory"]


def compute_rate(components: list[dict], output_qty, overheads: dict, margin_pct, margin_mode: str,
                 sources: dict[int, dict], places: int = 2) -> dict:
    """Deterministic rate build-up. All inputs are strings/Decimals; returns a transparent breakdown."""
    out_q = D(output_qty)
    if out_q <= 0:
        raise ValueError("Analysis output quantity must be positive")
    lines, warnings = [], []
    direct = ZERO
    recoverable_gst = ZERO
    for c in components:
        qty = D(c.get("qty", "0"))
        rate = try_D(c.get("rate"))
        src = sources.get(c.get("rate_source_id")) if c.get("rate_source_id") else None
        if src:
            rate = D(src["rate"])
            if src["source_type"] == "assumption":
                warnings.append(f"'{c.get('description')}' uses an assumed rate — confirm with a quotation.")
            if src["confidence"] < 0.8:
                warnings.append(f"'{c.get('description')}' rate source has low confidence ({src['confidence']}).")
            if src.get("stale"):
                warnings.append(f"'{c.get('description')}' quotation is past its validity date.")
        elif rate is not None:
            warnings.append(f"'{c.get('description')}' rate entered without a recorded source.")
        if rate is None:
            raise ValueError(f"Rate missing for component '{c.get('description')}'")
        wastage = D(c.get("wastage_pct") or "0")
        eff_qty = qty * (1 + wastage / 100)
        cost = eff_qty * rate
        gst_pct = D(src["gst_pct"]) if src else D(c.get("gst_pct") or "0")
        gst_amt = cost * gst_pct / 100
        gst_recoverable = src["gst_recoverable"] if src else bool(c.get("gst_recoverable", True))
        if gst_recoverable:
            recoverable_gst += gst_amt
            cost_in_rate = cost
        else:
            cost_in_rate = cost + gst_amt  # non-creditable tax is a real cost
        direct += cost_in_rate
        lines.append({"type": c.get("type"), "description": c.get("description"), "qty": str(qty), "wastage_pct": str(wastage),
                      "effective_qty": str(eff_qty), "rate": str(rate), "cost": str(q(cost_in_rate, 4)),
                      "gst": str(q(gst_amt, 4)), "gst_treatment": "input credit (excluded)" if gst_recoverable else "cost (non-recoverable)"})
    direct_per_unit = direct / out_q
    oh_total_pct = ZERO
    oh_lines = {}
    for k in OVERHEAD_KEYS:
        pct = D(overheads.get(k) or "0")
        oh_total_pct += pct
        oh_lines[k] = {"pct": str(pct), "amount": str(q(direct_per_unit * pct / 100, 4))}
    cost_per_unit = direct_per_unit * (1 + oh_total_pct / 100)
    m = D(margin_pct or "0")
    if margin_mode == "margin_on_sales":
        if m >= 100:
            raise ValueError("Margin on sales must be below 100%")
        price = cost_per_unit / (1 - m / 100)
        formula = "price = cost / (1 − margin%)"
    else:
        price = cost_per_unit * (1 + m / 100)
        formula = "price = cost × (1 + markup%)"
    rate = q(price, places)
    return {"rate": str(rate), "warnings": warnings,
            "breakdown": {"lines": lines, "direct_total": str(q(direct, 4)), "output_qty": str(out_q),
                          "direct_per_unit": str(q(direct_per_unit, 4)), "overheads_on_direct_cost": oh_lines,
                          "overhead_total_pct": str(oh_total_pct), "cost_per_unit": str(q(cost_per_unit, 4)),
                          "margin_mode": margin_mode, "margin_pct": str(m), "formula": formula,
                          "unrounded_price": str(q(price, 6)), "rounded_rate": str(rate),
                          "recoverable_gst_excluded": str(q(recoverable_gst / out_q, 4)),
                          "note": "Recoverable GST is excluded from cost (input credit); non-recoverable GST is included. "
                                  "Output GST on the bid is governed by the tender's tax clause and is not added here."}}


def quote_snapshot(items: list[dict], rates: dict[int, str | None], places: int, mode: str, pct_params: dict | None,
                   estimate: Decimal | None) -> dict:
    lines, missing = [], []
    total = ZERO
    for it in items:
        r = rates.get(it["id"])
        if r is None:
            missing.append(it["id"])
            lines.append({**it, "rate": None, "amount": None})
            continue
        amount = q(D(it["quantity"]) * D(r), places)
        total += amount
        lines.append({**it, "rate": str(D(r)), "amount": str(amount)})
    snap = {"mode": mode, "rounding_places": places, "lines": lines, "missing_rates": missing}
    if mode == "percentage":
        if estimate is None or not pct_params:
            raise ValueError("Percentage-rate bid needs the estimate and percentage")
        pct = D(pct_params["percent"])
        sign = -1 if pct_params.get("direction") == "below" else 1
        total = q(estimate * (1 + sign * pct / 100), places)
        snap["percentage"] = {"percent": str(pct), "direction": pct_params.get("direction", "above"), "estimate": str(estimate)}
    total = q(total, places)
    snap["total"] = str(total)
    snap["total_words"] = amount_in_words(total)
    snap["hash"] = hashlib.sha256(json.dumps({k: v for k, v in snap.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()
    return snap


SCENARIO_LIMITS = {"rate_change_pct": (-100, 500), "delay_months": (0, 120), "monthly_time_cost": (0, None),
                   "payment_cycle_days": (0, 730), "retention_pct": (0, 50), "retention_months": (0, 120),
                   "interest_pct_pa": (0, 60)}


def _num(case: str, field: str, value, default: str = "0") -> Decimal:
    """Parse one scenario input with a message a person can act on."""
    v = default if value is None or str(value).strip() == "" else value
    n = try_D(v)
    if n is None:
        raise ValueError(f"{case}: '{value}' is not a number for {field.replace('_', ' ')}")
    lo, hi = SCENARIO_LIMITS.get(field, (None, None))
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        rng = f"{lo} to {hi}" if hi is not None else f"at least {lo}"
        raise ValueError(f"{case}: {field.replace('_', ' ')} must be {rng} (got {value})")
    return n


def scenarios(base_total: Decimal, base_cost: Decimal, trade_totals: dict[str, Decimal], params: dict) -> dict:
    """Simple transparent sensitivity model. Every assumption is echoed back."""
    rate = _num("Settings", "interest_pct_pa", params.get("interest_pct_pa"), "10") / 100
    out = {"assumptions": params, "base": {"price": str(base_total), "cost": str(base_cost), "margin": str(base_total - base_cost)},
           "trade_costs": {k: str(q(v, 0)) for k, v in sorted(trade_totals.items())}}
    rows = []
    for i, s in enumerate(params.get("cases", [])):
        name = str(s.get("name") or f"Case {i + 1}")[:120]
        cost = base_cost
        notes = []
        for trade, pct in (s.get("rate_change_pct") or {}).items():
            p = _num(name, "rate_change_pct", pct)
            if not p:
                continue
            if trade not in trade_totals:
                notes.append(f"{trade} {p}%: no {trade} items priced in this quote")
                continue
            delta = trade_totals[trade] * p / 100
            cost += delta
            notes.append(f"{trade} {p}% → ₹{q(delta, 0)}")
        delay = _num(name, "delay_months", s.get("delay_months"))
        if delay:
            d = delay * _num(name, "monthly_time_cost", s.get("monthly_time_cost"))
            cost += d
            notes.append(f"delay {delay} months × time cost → ₹{q(d, 0)}")
        days = _num(name, "payment_cycle_days", s.get("payment_cycle_days"))
        if days:
            wc = base_total * rate * days / 365
            cost += wc
            notes.append(f"working capital {days} days → ₹{q(wc, 0)}")
        ret = _num(name, "retention_pct", s.get("retention_pct"))
        if ret:
            rc = base_total * ret / 100 * rate * _num(name, "retention_months", s.get("retention_months"), "12") / 12
            cost += rc
            notes.append(f"retention {ret}% finance cost → ₹{q(rc, 0)}")
        margin = base_total - cost
        rows.append({"name": name, "cost": str(q(cost, 0)), "margin": str(q(margin, 0)),
                     "margin_pct_of_price": str(q(margin / base_total * 100, 2)) if base_total else None, "notes": notes})
    out["cases"] = rows
    return out


# ------------------------------------------------------------------ original workbook export
def _cell_snapshot(wb) -> dict:
    snap = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    snap[(ws.title, c.coordinate)] = c.value if not isinstance(c.value, float) else repr(c.value)
        snap[(ws.title, "__merged__")] = sorted(str(m) for m in ws.merged_cells.ranges)
        snap[(ws.title, "__state__")] = (ws.sheet_state, bool(ws.protection.sheet))
    return snap


def export_priced_workbook(original: bytes, sheet: str, entries: dict[str, str], supported: bool) -> tuple[bytes, dict]:
    """Write ONLY the allowed rate cells into a copy of the original workbook and verify the result."""
    if not supported:
        raise BOQError("This workbook cannot be round-tripped safely. Enter rates manually in the official file "
                       "using the mapping report.")
    wb = openpyxl.load_workbook(io.BytesIO(original), keep_vba=False)
    ws = wb[sheet]
    for ref, val in entries.items():
        cell = ws[ref]
        if ws.protection.sheet and cell.protection.locked:
            raise BOQError(f"Cell {ref} is locked; protection is never bypassed.")
        if isinstance(cell.value, str) and cell.value.startswith("="):
            raise BOQError(f"Cell {ref} holds a formula; refusing to overwrite.")
        cell.value = float(D(val))  # Excel stores numbers as IEEE doubles; verified below
    buf = io.BytesIO()
    wb.save(buf)
    out = buf.getvalue()
    # verify by reopening both
    before = _cell_snapshot(openpyxl.load_workbook(io.BytesIO(original), keep_vba=False))
    after = _cell_snapshot(openpyxl.load_workbook(io.BytesIO(out), keep_vba=False))
    allowed = {(sheet, ref) for ref in entries}
    unexpected = []
    for k in set(before) | set(after):
        if k in allowed:
            continue
        if before.get(k) != after.get(k):
            unexpected.append({"cell": f"{k[0]}!{k[1]}", "before": str(before.get(k))[:80], "after": str(after.get(k))[:80]})
    written = []
    for (s, ref) in allowed:
        got = after.get((s, ref))
        if got is None or D(str(got) if not isinstance(got, str) else got) != D(entries[ref]):
            unexpected.append({"cell": f"{s}!{ref}", "issue": "value not written as expected"})
        else:
            written.append(ref)
    sheets_before = openpyxl.load_workbook(io.BytesIO(original), read_only=True).sheetnames
    sheets_after = openpyxl.load_workbook(io.BytesIO(out), read_only=True).sheetnames
    if sheets_before != sheets_after:
        unexpected.append({"issue": "sheet list changed"})
    report = {"ok": not unexpected, "cells_written": len(written), "unexpected_changes": unexpected[:50],
              "note": "Formula cells (amounts/totals) are preserved and recalculate when opened in Excel; "
                      "openpyxl does not compute formulas. Compare with TenderPilot's independent total."}
    if unexpected:
        raise BOQError(f"Verification failed; export withheld: {unexpected[:3]}")
    return out, report


def col_index(letter: str) -> int:
    return column_index_from_string(letter)
