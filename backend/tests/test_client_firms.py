"""Client firms library for a bid-submission company: profiles, credentials at a glance, single firm or JV per tender.
SYNTHETIC data."""
from __future__ import annotations

import json
from datetime import date, timedelta

from app.db import SessionLocal
from app.models import Tender
from app.services.docgen import build_context
from helpers import entity, tender


def _ev(org, eid, category, title, fields=None, expiry=None, verify=True):
    data = {"legal_entity_id": str(eid), "category": category, "title": title, "fields": json.dumps(fields or {})}
    if expiry:
        data["expiry_date"] = expiry
    r = org.post("/api/v1/evidence", files={"file": (f"{title}.pdf", b"%PDF-1.4 synthetic")}, data=data).json()
    if verify:
        org.post(f"/api/v1/evidence/{r['id']}/verify", json={"state": "verified"})
    return r


def _firm(org, name, **profile):
    e = entity(org, name)
    if profile:
        org.req("PUT", f"/api/v1/entities/{e['id']}", json={"profile": profile})
    return e


def test_profile_is_saved_and_never_wiped_by_a_partial_save(org):
    e = _firm(org, "Alpha Builders Pvt Ltd (SYNTHETIC)", udyam="UDYAM-XX-00-0000001", email="a@alpha.example",
              phone="0000000000", bank_account="123456789012", bank_ifsc="SYNT0000001", unknown_key="dropped")
    got = org.get(f"/api/v1/entities/{e['id']}?reveal=true")
    assert got["profile"]["udyam"] == "UDYAM-XX-00-0000001" and "unknown_key" not in got["profile"]
    # an older screen saving only the core fields keeps the profile
    org.req("PUT", f"/api/v1/entities/{e['id']}", json={"legal_name": "Alpha Builders Pvt Ltd (SYNTHETIC)", "signatory_name": "B. Rao"})
    got = org.get(f"/api/v1/entities/{e['id']}?reveal=true")
    assert got["signatory_name"] == "B. Rao" and got["profile"]["bank_ifsc"] == "SYNT0000001"
    # bank account masked unless revealed by someone allowed
    assert org.get(f"/api/v1/entities/{e['id']}")["profile"]["bank_account"] != "123456789012"
    assert got["summary"]["profile_missing"] == []


def test_summary_shows_what_the_firm_brings(org):
    e = _firm(org, "Beta Infra (SYNTHETIC)")
    _ev(org, e["id"], "turnover_certificate", "CA cert 24-25", {"fy": "2024-25", "turnover": "120000000"})
    _ev(org, e["id"], "audited_accounts", "Accounts 25-26", {"fy": "2025-26", "turnover": "150000000", "net_worth": "40000000"})
    _ev(org, e["id"], "completion_certificate", "WTP Demo", {"work_name": "5 MLD WTP", "executed_value": "90000000"})
    _ev(org, e["id"], "machinery", "Excavators", {"equipment_type": "Hydraulic excavator", "quantity": "3"})
    _ev(org, e["id"], "registration", "PWD Class I", {"authority": "PWD", "class": "I"},
        expiry=(date.today() - timedelta(days=5)).isoformat())
    _ev(org, e["id"], "gst", "GST certificate", expiry=(date.today() + timedelta(days=10)).isoformat(), verify=False)
    s = org.get(f"/api/v1/entities/{e['id']}")["summary"]
    assert [x["fy"] for x in s["turnover_by_fy"]] == ["2025-26", "2024-25"]
    assert s["net_worth"]["amount"] == "40000000"
    assert s["works"] == {"count": 1, "total_value": "90000000"} and s["machinery_units"] == "3"
    assert s["counts"]["financial"] == 2 and s["counts"]["registrations"] == 2
    assert s["expired"] == 1 and s["expiring_30_days"] == 1 and s["unverified"] == 1
    assert "E-mail" in s["profile_missing"]
    listed = next(x for x in org.get("/api/v1/entities?summary=true") if x["id"] == e["id"])
    assert listed["summary"]["total"] == 6 and listed["tender_count"] == 0


def test_single_firm_chosen_from_the_library(org):
    e = _firm(org, "Gamma Constructions (SYNTHETIC)")
    t = tender(org)
    out = org.req("PUT", f"/api/v1/tenders/{t['id']}/participants", json={"mode": "single", "entity_id": e["id"], "row_version": t["row_version"]}).json()
    assert out["tender"]["bidder_entity_id"] == e["id"] and out["participants"]["mode"] == "single"
    assert out["participants"]["firms"][0]["legal_name"] == "Gamma Constructions (SYNTHETIC)"
    assert org.get(f"/api/v1/entities/{e['id']}")["summary"]["tenders"][0]["role"] == "Single bidder"


def test_jv_built_from_library_firms_for_this_tender(org):
    a, b = _firm(org, "Lead Co (SYNTHETIC)"), _firm(org, "Partner Co (SYNTHETIC)")
    _ev(org, b["id"], "turnover_certificate", "Partner turnover", {"fy": "2025-26", "turnover": "50000000"})
    t = tender(org)
    body = {"mode": "jv", "row_version": t["row_version"], "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "60"}, {"legal_entity_id": b["id"], "role": "member", "share_pct": "40"}]}
    out = org.req("PUT", f"/api/v1/tenders/{t['id']}/participants", json=body).json()
    p = out["participants"]
    assert p["mode"] == "jv" and p["jv"]["tender_specific"] and p["jv"]["name"] == "Lead Co (SYNTHETIC) – Partner Co (SYNTHETIC) (JV)"
    assert [f["role"] for f in p["firms"]] == ["lead", "member"]
    assert p["firms"][1]["summary"]["turnover_by_fy"][0]["amount"] == "50000000"  # credentials used as they are
    jv_id = out["tender"]["jv_id"]
    # changing shares updates the same tender JV
    body["members"][0]["share_pct"], body["members"][1]["share_pct"] = "51", "49"
    body["row_version"] = out["tender"]["row_version"]
    out2 = org.req("PUT", f"/api/v1/tenders/{t['id']}/participants", json=body).json()
    assert out2["tender"]["jv_id"] == jv_id and out2["participants"]["firms"][0]["share_pct"] == "51"
    # tenders listed per client firm (lead and member)
    assert [x["id"] for x in org.get(f"/api/v1/tenders?firm={b['id']}")] == [t["id"]]
    assert "JV member (49" in org.get(f"/api/v1/entities/{b['id']}")["summary"]["tenders"][0]["role"]
    # forms read the lead firm and JV name
    db = SessionLocal()
    try:
        ctx = build_context(db, db.get(Tender, t["id"]))
    finally:
        db.close()
    assert ctx["jv.lead_name"] == "Lead Co (SYNTHETIC)" and "Partner Co (SYNTHETIC) (member, 49" in ctx["jv.members"]


def test_library_jv_is_not_changed_by_a_tender(org):
    a, b, c = (_firm(org, f"Firm {x} (SYNTHETIC)") for x in "ABC")
    lib = org.post("/api/v1/jvs", json={"name": "Saved JV", "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "50"}, {"legal_entity_id": b["id"], "role": "member", "share_pct": "50"}]}).json()
    t = tender(org, jv_id=lib["id"])
    out = org.req("PUT", f"/api/v1/tenders/{t['id']}/participants", json={"mode": "jv", "row_version": t["row_version"], "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "70"}, {"legal_entity_id": c["id"], "role": "member", "share_pct": "30"}]}).json()
    assert out["tender"]["jv_id"] != lib["id"]
    saved = next(j for j in org.get("/api/v1/jvs") if j["id"] == lib["id"])
    assert {m["legal_entity_id"] for m in saved["members"]} == {a["id"], b["id"]}


def test_jv_rules_are_checked(org):
    a, b = _firm(org, "One (SYNTHETIC)"), _firm(org, "Two (SYNTHETIC)")
    t = tender(org)
    url, rv = f"/api/v1/tenders/{t['id']}/participants", t["row_version"]
    m = lambda ra, sa, rb, sb: [{"legal_entity_id": a["id"], "role": ra, "share_pct": sa}, {"legal_entity_id": b["id"], "role": rb, "share_pct": sb}]  # noqa: E731
    org.req("PUT", url, json={"mode": "jv", "row_version": rv, "members": m("lead", "60", "member", "30")}, expect=422)  # 90 %
    org.req("PUT", url, json={"mode": "jv", "row_version": rv, "members": m("lead", "50", "lead", "50")}, expect=422)  # two leads
    org.req("PUT", url, json={"mode": "jv", "row_version": rv, "members": m("lead", "100", "member", "0")}, expect=422)
    org.req("PUT", url, json={"mode": "jv", "row_version": rv, "members": m("lead", "50", "member", "50")[:1]}, expect=422)
    org.req("PUT", url, json={"mode": "single", "row_version": rv}, expect=422)
    org.req("PUT", url, json={"mode": "single", "entity_id": a["id"], "row_version": rv}, role="auditor", expect=403)


def test_other_organisations_firms_cannot_be_used(org, other_org):
    theirs = entity(other_org, "Not yours (SYNTHETIC)")
    t = tender(org)
    org.req("PUT", f"/api/v1/tenders/{t['id']}/participants", json={"mode": "single", "entity_id": theirs["id"], "row_version": t["row_version"]}, expect=404)
    org.req("GET", f"/api/v1/entities/{theirs['id']}", expect=404)


def test_submitting_company_profile(org):
    out = org.req("PUT", "/api/v1/org/profile", json={"name": "Bid Desk Consultants (SYNTHETIC)", "address": "2 Demo Lane",
                                                     "phone": "0000000001", "email": "bids@desk.example"}).json()
    assert out["name"] == "Bid Desk Consultants (SYNTHETIC)" and org.get("/api/v1/org/profile")["email"] == "bids@desk.example"
    org.req("PUT", "/api/v1/org/profile", json={"name": "x"}, role="tender_manager", expect=403)
    t = tender(org)
    db = SessionLocal()
    try:
        ctx = build_context(db, db.get(Tender, t["id"]))
    finally:
        db.close()
    assert ctx["agency.name"] == "Bid Desk Consultants (SYNTHETIC)" and ctx["agency.address"] == "2 Demo Lane"
