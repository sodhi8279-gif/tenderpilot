"""Personnel and machinery tables continued on the next page (reader-3). SYNTHETIC."""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.extract import Candidate, extract_continuations, extract_from_page, merge_candidates
from fixtures.synthetic import _text_page
from helpers import reqs, tender, upload

P1 = [
    "SECTION 3 - QUALIFICATION CRITERIA (SYNTHETIC)",
    "8.5 The bidder shall have registration in Class I with the State PWD.",
    "8.6 Key personnel: The bidder shall deploy the following key personnel:",
    "Sl. No. Designation Qualification Experience Nos.",
    "(i) Project Manager - B.E./B.Tech (Civil) with 12 years experience - 1 No.",
    "(ii) Site Engineer - B.E. (Civil) or Diploma (Civil) with 5 years experience - 2 Nos.",
    "Page 4 of 12",
]
P2 = [
    "Sl. No. Designation Qualification Experience Nos.",
    "(iii) Mechanical Engineer - B.E. (Mechanical) with 5 years experience - 1 No.",
    "(iv) Electrical Engineer - B.E. (Electrical) with 3 years experience - 1 No.",
    "8.7 Key plant and machinery: The bidder should own or have on lease the following:",
    "(i) Hydraulic excavator - 2 Nos.",
    "(ii) Crane of capacity 10 MT - 1 No.",
    "(iii) DG set 125 kVA",
    "Page 5 of 12",
]
P3 = [
    "- 1 No.",  # never reaches here: see the fragment case below
    "(iv) Hydrostatic test pump - 1 No.",
    "8.8 Qualification shall be assessed as on the last date of submission of bids.",
]


def _pdf(pages) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for p in pages:
        _text_page(c, p)
    c.save()
    return buf.getvalue()


def _read(pages):
    base = []
    for i, t in enumerate(pages):
        base.extend(extract_from_page("\n".join(t), i, None))
    cont = extract_continuations([("\n".join(t), i, None) for i, t in enumerate(pages)], base)
    return {c.key: c for c in merge_candidates(base + cont)}, cont


def test_rows_on_the_next_page_are_read_under_the_open_heading():
    got, cont = _read([P1, P2])
    for role in ("project_manager", "site_engineer", "mechanical_engineer", "electrical_engineer"):
        assert f"tech.personnel.{role}" in got, sorted(got)
    mech = got["tech.personnel.mechanical_engineer"]
    assert mech.page_index == 1 and mech.value["count"] == 1 and mech.value["min_experience_years"] == 5
    assert any("from page 1" in n for n in mech.notes)
    # The repeated column headings and page numbers are not read as rows.
    assert not any("designation" in k or "page" in k for k in got)


def test_closed_sections_are_not_extended():
    closed = P1[:-1] + ["8.6.1 The personnel shall be on the bidder's rolls.", "9. JOINT VENTURE"]
    _, cont = _read([closed, P2])
    assert cont == []


def test_row_cut_by_the_page_break_is_joined():
    p1 = P1[:-1] + ["(iii) Mechanical Engineer - B.E. (Mechanical)"]
    p2 = ["with 7 years experience - 1 No.", "8.7 Key plant and machinery: The bidder should own the following:",
          "(i) Hydraulic excavator - 2 Nos."]
    got, _ = _read([p1, p2])
    mech = got["tech.personnel.mechanical_engineer"]
    assert mech.value["min_experience_years"] == 7 and mech.page_index == 1
    assert any("ends on page 2" in n for n in mech.notes)


def test_same_post_with_different_requirement_gets_its_own_row():
    p2 = ["(iii) Site Engineer - Diploma (Electrical) with 3 years experience - 1 No.", "9. JOINT VENTURE"]
    got, _ = _read([P1, p2])
    assert got["tech.personnel.site_engineer"].value["count"] == 2
    assert got["tech.personnel.site_engineer_2"].value["min_experience_years"] == 3


def test_split_tables_through_the_upload_flow(org):
    t = tender(org)
    upload(org, t["id"], _pdf([P1, P2, P3]), "qualification.pdf", "qualification")
    r = reqs(org, t["id"])
    assert {"tech.personnel.mechanical_engineer", "tech.personnel.electrical_engineer"} <= set(r)
    assert r["tech.personnel.electrical_engineer"]["current"]["anchor"]["page_index"] == 1
    assert "tech.equipment.hydrostatic_test_pump" in r or any(k.startswith("tech.equipment.") and "test" in k for k in r)
