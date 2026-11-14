"""Tender forms as PDF: fillable fields mapped and filled; flat/scanned forms filled at placed positions. SYNTHETIC."""
from __future__ import annotations

import fitz

from fixtures.synthetic import simple_pdf
from helpers import entity, tender


def _acroform() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "FORM OF BID (SYNTHETIC)", fontsize=14)
    for i, (name, y) in enumerate((("bidder_name", 120), ("signatory_name", 160), ("tender_ref", 200))):
        page.insert_text((72, y + 14), name.replace("_", " ").title() + ":", fontsize=10)
        w = fitz.Widget()
        w.field_type, w.field_name, w.rect = fitz.PDF_WIDGET_TYPE_TEXT, name, fitz.Rect(200, y, 520, y + 20)
        page.add_widget(w)
    w = fitz.Widget()
    w.field_type, w.field_name, w.rect = fitz.PDF_WIDGET_TYPE_CHECKBOX, "msme", fitz.Rect(200, 240, 214, 254)
    page.add_widget(w)
    return doc.tobytes()


def _upload(org, tid, data, name):
    return org.post(f"/api/v1/tenders/{tid}/templates", files={"file": (name, data)},
                    data={"name": name, "envelope": "technical"}).json()


def _field_values(pdf: bytes) -> dict:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return {w.field_name: w.field_value for p in d for w in p.widgets()}


def test_fillable_pdf_fields_are_mapped_and_filled(org):
    e = entity(org)
    t = org.post("/api/v1/tenders", json={"title": "Form test (SYNTHETIC)", "tender_ref": "SYN/PDF/01", "department": "Demo",
                                          "bidder_type": "entity", "bidder_entity_id": e["id"]}).json()
    original = _acroform()
    tpl = _upload(org, t["id"], original, "form-of-bid.pdf")
    assert tpl["kind"] == "pdf_form" and set(tpl["placeholders"]) == {"bidder_name", "signatory_name", "tender_ref", "msme"}
    guesses = {m["placeholder"]: m["source_path"] for m in tpl["mappings"]}
    assert guesses["tender_ref"] == "tender.tender_ref" and guesses["signatory_name"] == "entity.signatory_name"
    # Generation waits for confirmed mappings.
    org.post(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/generate", expect=409)
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/mappings", json=[
        {"placeholder": "bidder_name", "source_path": "entity.legal_name"},
        {"placeholder": "signatory_name", "source_path": "entity.signatory_name"},
        {"placeholder": "tender_ref", "source_path": "tender.tender_ref"},
        {"placeholder": "msme", "manual_value": "yes"}])
    g = org.post(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/generate").json()
    assert g["status"] == "draft" and g["missing_fields"] == [] and g["docx_key"] is None
    pdf = org.req("GET", f"/api/v1/tenders/{t['id']}/generated/{g['id']}/pdf").content
    vals = _field_values(pdf)
    assert vals["bidder_name"] == e["legal_name"] and vals["tender_ref"] == "SYN/PDF/01" and vals["signatory_name"] == "A. Kumar"
    assert vals["msme"] not in ("Off", "", None)
    # The uploaded form itself is unchanged (its fields are still empty).
    assert all(v in ("", None, "Off") for v in _field_values(original).values())


def test_missing_value_is_printed_and_recorded(org):
    t = tender(org)  # no bidder chosen: entity fields are missing
    tpl = _upload(org, t["id"], _acroform(), "f.pdf")
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/mappings", json=[
        {"placeholder": p, "source_path": "entity.legal_name"} for p in ("bidder_name", "signatory_name", "tender_ref")]
        + [{"placeholder": "msme", "manual_value": "no"}])
    g = org.post(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/generate").json()
    assert "bidder_name" in g["missing_fields"]
    pdf = org.req("GET", f"/api/v1/tenders/{t['id']}/generated/{g['id']}/pdf").content
    assert _field_values(pdf)["bidder_name"] == "«MISSING: bidder_name»"


def test_scanned_form_values_are_printed_at_placed_boxes(org):
    e = entity(org)
    t = org.post("/api/v1/tenders", json={"title": "Overlay (SYNTHETIC)", "tender_ref": "SYN/OVL/02", "department": "Demo",
                                          "bidder_type": "entity", "bidder_entity_id": e["id"]}).json()
    flat = simple_pdf("AFFIDAVIT FORMAT (SYNTHETIC)", ["Name of bidder: ____________________", "Tender No.: ______________"])
    tpl = _upload(org, t["id"], flat, "affidavit.pdf")
    assert tpl["kind"] == "pdf_overlay" and tpl["placeholders"] == []
    info = org.get(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/pdf")
    assert len(info["pages"]) == 1 and info["fields"] == []
    assert org.req("GET", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/pdf/pages/0").content[:4] == b"\x89PNG"
    org.post(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/generate", expect=409)  # no boxes yet
    bad = [{"name": "x", "page": 3, "x": 0.1, "y": 0.1}]
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/layout", json=bad, expect=422)
    out = org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/layout", json=[
        {"name": "legal_name", "page": 1, "x": 0.35, "y": 0.2, "width": 0.5, "font_size": 11},
        {"name": "tender_ref", "page": 1, "x": 0.3, "y": 0.25, "width": 0.4}]).json()
    assert {m["placeholder"]: m["source_path"] for m in out["mappings"]} == {"legal_name": "entity.legal_name", "tender_ref": "tender.tender_ref"}
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/mappings", json=[
        {"placeholder": "legal_name", "source_path": "entity.legal_name"}, {"placeholder": "tender_ref", "source_path": "tender.tender_ref"}])
    g = org.post(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/generate").json()
    pdf = org.req("GET", f"/api/v1/tenders/{t['id']}/generated/{g['id']}/pdf").content
    with fitz.open(stream=pdf, filetype="pdf") as d:
        text = d[0].get_text()
        words = d[0].get_text("words")
    assert "SYN/OVL/02" in text and e["legal_name"].split()[0] in text
    ref = next(w for w in words if w[4] == "SYN/OVL/02")
    assert abs(ref[0] / 595.27 - 0.3) < 0.02  # printed where the box was placed
    # Removing a box removes its mapping; the original form is untouched.
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/layout", json=[{"name": "tender_ref", "page": 1, "x": 0.3, "y": 0.25}])
    assert [m["placeholder"] for m in org.get(f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/mappings")] == ["tender_ref"]


def test_price_fields_still_blocked_in_technical_pdf_forms(org):
    t = tender(org)
    tpl = _upload(org, t["id"], _acroform(), "f.pdf")
    org.req("PUT", f"/api/v1/tenders/{t['id']}/templates/{tpl['id']}/mappings",
            json=[{"placeholder": "bidder_name", "source_path": "quote.total"}], expect=409)
