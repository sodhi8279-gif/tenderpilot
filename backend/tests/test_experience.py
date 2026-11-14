"""General / specific experience, machinery lists and manpower in running text, in the layout of a real state-PSU NIT.
All data is SYNTHETIC."""
from __future__ import annotations

from fixtures.synthetic import DEADLINE, EXP_NIT_PAGES, _fmt, experience_nit_pdf
from helpers import entity, evidence, set_cutoff, tender, upload

from app.services.extract import extract_from_page, merge_candidates
from app.services.technical import keywords_match, nature_keywords, parse_dimensions

CUTOFF = DEADLINE.isoformat()
ESTIMATE = 67_956_000  # 6.7956 Crore


def _cands():
    out = []
    for i, page in enumerate(EXP_NIT_PAGES):
        out += extract_from_page("\n".join(_fmt(page)), i, None)
    return {c.key: c for c in merge_candidates(out)}


def test_experience_machinery_and_manpower_are_read_from_the_psu_layout():
    c = _cands()
    est = c["commercial.estimate"].value
    assert est["amount"].startswith("67956000")
    reg = c["tech.registration_class"].value
    assert reg["class"] == "A" and reg["or_above"] is True
    assert c["jv.permitted"].value == {"permitted": True}  # the ban on *foreign* JVs is not a ban on JVs
    assert c["jv.max_members"].value == {"max_members": 2}

    for n, pct in ((1, "80"), (2, "50"), (3, "40")):
        g = c[f"exp.similar_work.{n}"].value
        assert g["count"] == n and g["percent_of_estimate"] == pct and g["period_years"] == 10
        assert g["work_nature"] == "construction of retaining wall" and g["allow_ongoing"] is True
        assert g["dimensions"] == [{"name": "height", "min": "7.5", "unit": "m"}, {"name": "length", "min": "10", "unit": "m"}]

    spec = [v for k, v in c.items() if k.startswith("exp.specific.")]
    assert len(spec) == 1
    s = spec[0].value
    assert s["work"] == "Construction of RCC/PCC slope protection works with anchor bolts"
    assert s["dimensions"] == [{"name": "height", "min": "5", "unit": "m"}, {"name": "length", "min": "20", "unit": "m"}]
    assert s["count"] == 1 and s["period_years"] == 10
    assert s["jv_hint"]["mode"] == "any_member"

    eq = {k.split(".", 2)[2]: v.value for k, v in c.items() if k.startswith("tech.equipment.")}
    assert set(eq) == {"batching_plant", "transit_mixer", "concrete_pump", "drilling_rig", "grouting_equipment"}
    assert eq["drilling_rig"]["min_capacity"] == "45" and eq["drilling_rig"]["capacity_unit"] == "mm"
    assert eq["batching_plant"]["ownership"] == "owned_or_leased"

    pers = {k: v.value for k, v in c.items() if k.startswith("tech.personnel.")}
    assert set(pers) == {"tech.personnel.civil_engineer", "tech.personnel.safety_officer"}
    assert pers["tech.personnel.civil_engineer"]["min_experience_years"] == 5
    assert pers["tech.personnel.civil_engineer"]["disciplines"] == ["civil"]

    split = c["jv.criteria_split"].value  # JV notes on pages 3 and 4 are combined, not reported as a conflict
    assert split["general_mode"] == "aggregate_sum" and split["specific_mode"] == "any_member"
    assert split["lead_min_pct"] == "50" and split["applies_clause"] == "1.6(B)"


def test_dimension_and_nature_helpers():
    assert parse_dimensions("8 m high and 120 m long") == [{"name": "height", "min": "8", "unit": "m"},
                                                           {"name": "length", "min": "120", "unit": "m"}]
    assert parse_dimensions("H = 9.5 m, L: 40 m") == [{"name": "height", "min": "9.5", "unit": "m"},
                                                      {"name": "length", "min": "40", "unit": "m"}]
    kw = nature_keywords("Construction of RCC/PCC slope protection works with anchor bolts")
    assert kw == ["rcc/pcc", "slope", "protection", "anchor", "bolt"]
    assert keywords_match(kw, "Slope protection with PCC breast wall and rock anchoring")[0]
    assert not keywords_match(["retaining", "wall"], "Bituminous road resurfacing")[0]


def _tender(org, **kw):
    t = tender(org, **kw)
    upload(org, t["id"], experience_nit_pdf(), "EXP_NIT.pdf")
    set_cutoff(org, t["id"], CUTOFF)
    return t["id"]


def _approve(org, tid, use_jv=False):
    for r in org.get(f"/api/v1/tenders/{tid}/rules"):
        if r["status"] != "proposed":
            continue
        params = dict(r["params"])
        if use_jv and params.get("jv_suggested"):
            params["jv"] = {k: v for k, v in params["jv_suggested"].items() if k != "basis"}
        org.post(f"/api/v1/tenders/{tid}/rules/{r['id']}/approve", json={"params": params, "confirm_low_confidence": True})


def _exp(evals):
    out = {}
    for e in evals:
        rt = e["rule"]["rule_type"]
        if rt == "similar_work":
            out.setdefault(f"general{e['rule']['params']['count']}", []).append(e)
        elif rt == "specific_experience":
            out.setdefault("specific", []).append(e)
    return out


def test_experience_is_exercised_against_certificates_with_size_and_per_tender_similarity(org):
    a = entity(org, "Ridge Builders Private Limited (SYNTHETIC)")
    tid = _tender(org, entity_id=a["id"])
    wall = evidence(org, a["id"], "completion_certificate", "Retaining wall, Demo Hills road", {
        "work_name": "Construction of RCC retaining wall at Demo Hills road", "scope_of_work": "RCC retaining wall 8 m high, 60 m long",
        "executed_value": "56000000", "completion_date": "2023-03-31", "status": "completed"})
    short = evidence(org, a["id"], "completion_certificate", "Slope protection, Demo Ghat", {
        "work_name": "Slope protection with RCC facing and anchor bolts", "dimensions": "height 4 m, length 30 m",
        "executed_value": "9000000", "completion_date": "2022-06-30", "status": "completed"})
    tall = evidence(org, a["id"], "completion_certificate", "Slope stabilisation, Demo Pass", {
        "work_name": "Slope stabilisation with PCC breast wall and rock anchor bolts", "dimensions": "H = 6 m, L = 25 m",
        "executed_value": "12000000", "completion_date": "2024-01-15", "status": "completed"})
    road = evidence(org, a["id"], "completion_certificate", "Road resurfacing", {
        "work_name": "Bituminous resurfacing of district road", "executed_value": "70000000",
        "completion_date": "2023-09-30", "status": "completed"})
    rules = org.get(f"/api/v1/tenders/{tid}/rules")
    kinds = {r["rule_type"] for r in rules}
    assert {"similar_work", "specific_experience", "equipment", "personnel", "registration_class"} <= kinds
    _approve(org, tid)

    ev = _exp(org.post(f"/api/v1/tenders/{tid}/evaluate").json()["evaluations"])
    g1 = ev["general1"][0]
    assert g1["result"] == "insufficient_evidence"  # the wall matches in kind, size and value, but a person must confirm
    rows = {w["evidence_id"]: w for w in g1["calculation"]["works"]}
    assert "confirm it is similar" in rows[wall["id"]]["result"]
    assert rows[road["id"]]["similarity"] == "no_match"
    sp = ev["specific"][0]
    srows = {w["evidence_id"]: w for w in sp["calculation"]["works"]}
    assert srows[short["id"]]["result"].startswith("smaller than required: height 4")
    assert "confirm" in srows[tall["id"]]["result"]

    key_g, key_s = g1["calculation"]["similarity_key"], sp["calculation"]["similarity_key"]
    for eid, key in ((wall["id"], key_g), (tall["id"], key_s)):
        r = org.post(f"/api/v1/tenders/{tid}/similarity", json={"evidence_id": eid, "criterion_key": key, "similar": True})
        assert r.status_code == 200, r.text
    # a road job is not similar for this tender, even if someone marks it so for another one
    org.post(f"/api/v1/tenders/{tid}/similarity", json={"evidence_id": road["id"], "criterion_key": key_g, "similar": False})

    ev = _exp(org.post(f"/api/v1/tenders/{tid}/evaluate").json()["evaluations"])
    assert ev["general1"][0]["calculation"]["preview_result"] == "pass"  # 5.6 Cr >= 80% of 6.7956 Cr
    assert ev["general2"][0]["calculation"]["preview_result"] == "fail"  # only one wall
    assert ev["specific"][0]["calculation"]["preview_result"] == "pass"

    plan = org.get(f"/api/v1/tenders/{tid}/qualification-plan")
    labels = {c["label"] for c in plan["criteria"]}
    assert {"General experience", "Specific experience", "Plant & machinery", "Key personnel"} <= labels


def test_jv_partners_experience_is_aggregated_and_specific_experience_from_any_partner(org):
    a = entity(org, "Ridge Builders Private Limited (SYNTHETIC)")
    b = entity(org, "Anchor Geotech Private Limited (SYNTHETIC)")
    for ent, val in ((a, "30000000"), (b, "36000000")):
        evidence(org, ent["id"], "completion_certificate", "Retaining wall", {
            "work_name": "RCC retaining wall", "scope_of_work": "height 9 m, length 80 m", "executed_value": val,
            "completion_date": "2023-12-31", "status": "completed", "is_similar": True})
    evidence(org, b["id"], "completion_certificate", "Slope protection", {
        "work_name": "RCC slope protection with anchor bolts", "dimensions": "height 6 m, length 40 m",
        "executed_value": "8000000", "completion_date": "2024-02-28", "status": "completed", "is_similar": True})
    jv = org.post("/api/v1/jvs", json={"name": "Ridge-Anchor JV (SYNTHETIC)", "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "51"},
        {"legal_entity_id": b["id"], "role": "member", "share_pct": "49"}]}).json()
    tid = _tender(org, jv_id=jv["id"])
    rules = {r["rule_type"] + str(r["params"].get("count", "")): r for r in org.get(f"/api/v1/tenders/{tid}/rules")}
    assert rules["similar_work2"]["params"]["jv_suggested"]["mode"] == "aggregate_sum"
    assert rules["specific_experience1"]["params"]["jv_suggested"]["mode"] == "any_member"
    _approve(org, tid, use_jv=True)
    evals = org.post(f"/api/v1/tenders/{tid}/evaluate").json()["evaluations"]
    agg = {(e["rule"]["rule_type"], e["rule"]["params"].get("count")): e for e in evals if e["subject"] == "jv:aggregate"}
    # two walls of 3.0 Cr and 3.6 Cr: each >= 40% (2.72 Cr) -> 2 x 50% fails (3.40 Cr), 3 x 40% needs three
    assert agg[("similar_work", 2)]["calculation"]["preview_result"] == "fail"
    assert agg[("similar_work", 3)]["calculation"]["qualifying_count"] == 2
    assert agg[("specific_experience", 1)]["calculation"]["preview_result"] == "pass"  # the partner has it
