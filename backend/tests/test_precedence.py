"""Order of precedence: suggested ranks from the clause, governing value for conflicts, reviewer applies. SYNTHETIC."""
from __future__ import annotations

from fixtures.synthetic import simple_pdf
from helpers import reqs, tender, upload

GCC = simple_pdf("GENERAL CONDITIONS OF CONTRACT (SYNTHETIC)", [
    "4. Earnest Money Deposit (EMD): Rs. 10,00,000 by online payment.",
    "5. Order of Precedence of documents: The documents forming the contract shall be interpreted in the following",
    "order of priority: (a) Special Conditions of Contract; (b) General Conditions of Contract; (c) Technical",
    "Specifications; (d) Drawings; (e) Bill of Quantities.",
])
SCC = simple_pdf("SPECIAL CONDITIONS OF CONTRACT (SYNTHETIC)", [
    "2. Earnest Money Deposit (EMD): Rs. 12,50,000 by online payment or bank guarantee.",
])


def _setup(org):
    t = tender(org)
    gcc = upload(org, t["id"], GCC, "gcc.pdf", "gcc")
    scc = upload(org, t["id"], SCC, "scc.pdf", "scc")
    return t, gcc, scc


def test_ranks_are_suggested_from_the_precedence_clause(org):
    t, gcc, scc = _setup(org)
    p = org.get(f"/api/v1/tenders/{t['id']}/precedence")
    assert p["clause_found"] is True
    assert p["order"][:5] == ["scc", "gcc", "specification", "drawing", "boq"]
    by_name = {d["filename"]: d for d in p["documents"]}
    assert by_name["scc.pdf"]["suggested_rank"] == 1 and by_name["gcc.pdf"]["suggested_rank"] == 2
    assert by_name["scc.pdf"]["precedence_rank"] is None  # only a suggestion until confirmed


def test_conflict_needs_ranks_then_governing_value_is_applied_by_reviewer(org):
    t, gcc, scc = _setup(org)
    emd = reqs(org, t["id"])["commercial.emd"]
    assert emd["has_conflict"] is True
    assert emd["precedence"]["status"] == "ranks_missing"
    # Recording ranks changes no requirement by itself.
    org.req("PUT", f"/api/v1/tenders/{t['id']}/documents/{scc['id']}/precedence", json={"precedence_rank": 1})
    org.req("PUT", f"/api/v1/tenders/{t['id']}/documents/{gcc['id']}/precedence", json={"precedence_rank": 2})
    emd = reqs(org, t["id"])["commercial.emd"]
    assert emd["has_conflict"] is True
    g = emd["precedence"]
    assert g["status"] == "suggested" and g["document"] == "scc.pdf" and g["value"]["amount"] == "1250000"
    assert emd["current"]["value"]["amount"] == "1000000"  # still the first reading until a reviewer acts
    # An auditor cannot apply it; a reviewer can.
    org.post(f"/api/v1/tenders/{t['id']}/requirements/{emd['id']}/apply-precedence", role="auditor", expect=403)
    out = org.post(f"/api/v1/tenders/{t['id']}/precedence/apply-all", role="technical_reviewer").json()
    assert out["applied"] >= 1 and emd["id"] in out["requirement_ids"]
    emd = reqs(org, t["id"])["commercial.emd"]
    assert emd["has_conflict"] is False and emd["current"]["value"]["amount"] == "1250000"
    assert emd["current"]["review_status"] == "approved" and "order of precedence" in emd["current"]["review_note"]
    # History keeps both readings.
    assert {v["value"].get("amount") for v in emd["versions"]} >= {"1000000", "1250000"}


def test_equal_ranks_are_left_to_the_reviewer(org):
    t, gcc, scc = _setup(org)
    for d in (gcc, scc):
        org.req("PUT", f"/api/v1/tenders/{t['id']}/documents/{d['id']}/precedence", json={"precedence_rank": 1})
    emd = reqs(org, t["id"])["commercial.emd"]
    assert emd["precedence"]["status"] == "tie"
    assert org.post(f"/api/v1/tenders/{t['id']}/precedence/apply-all").json()["applied"] == 0
