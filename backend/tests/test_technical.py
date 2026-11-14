"""Technical qualification criteria and the single-company / JV qualification planner. All data is SYNTHETIC."""
from __future__ import annotations

from fixtures.synthetic import DEADLINE, TECH_NIT_PAGES, TECH_PARTNERS, _fmt, technical_nit_pdf
from helpers import entity, evidence, set_cutoff, tender, upload

from app.services.extract import extract_from_page, merge_candidates
from app.services.technical import (equipment_matches, item_matches, min_qualification, norm_unit, parse_equipment_line,
                                    parse_personnel_line, parse_quantity_line)

CUTOFF = DEADLINE.isoformat()


def _cands():
    out = []
    for i, page in enumerate(TECH_NIT_PAGES):
        out += extract_from_page("\n".join(_fmt(page)), i, None)
    return {c.key: c for c in merge_candidates(out)}


def test_technical_criteria_are_read_with_values_and_clauses():
    c = _cands()
    pm = c["tech.personnel.project_manager"]
    assert pm.value == {"role": "Project Manager", "count": 1, "min_qualification": "graduate", "disciplines": ["civil"],
                        "min_experience_years": 12}
    assert pm.clause_no == "8.6(i)"
    se = c["tech.personnel.site_engineer"].value
    assert se["count"] == 2 and se["min_qualification"] == "diploma"  # "B.E. or Diploma" accepts a diploma
    assert c["tech.personnel.mechanical_engineer"].value["disciplines"] == ["mechanical"]
    assert c["tech.equipment.excavator"].value["count"] == 2
    crane = c["tech.equipment.crane"].value
    assert crane["min_capacity"] == "10" and crane["capacity_unit"] == "MT" and crane["ownership"] == "owned_or_leased"
    assert c["tech.equipment.dg_set"].value["capacity_unit"] == "kVA"
    pipe = c["tech.quantity.pipe_laying"].value
    assert pipe["min_quantity"] == "15" and pipe["unit"] == "km" and pipe["min_dia_mm"] == 300 and pipe["material"] == "DI"
    assert pipe["basis"] == "any_one_year" and pipe["period_years"] == 5
    assert c["tech.quantity.earthwork"].value["min_quantity"] == "50000"
    reg = c["tech.registration_class"].value
    assert reg["class"] == "I" and reg["authority"] == "CPWD" and reg["or_above"] is True
    assert "pumping main" in c["tech.similar_definition"].value["definition"]
    split = c["jv.criteria_split"].value
    assert split["lead_min_pct"] == "60" and split["member_min_pct"] == "30" and split["combined"] is True
    assert split["similar_work_mode"] == "lead_only" and split["net_worth_mode"] == "each_member"


def test_line_parsers_handle_common_layouts():
    p = parse_personnel_line("2. Two Graduate Civil Engineers having 10 years experience")
    assert p["role"] == "Graduate Civil Engineer" and p["count"] == 2 and p["min_qualification"] == "graduate"
    e = parse_equipment_line("4 Nos. Transit Mixers of 6 cum capacity")
    assert e["equipment"] == "Transit Mixers" and e["count"] == 4 and e["min_capacity"] == "6"
    e = parse_equipment_line("Concrete batching plant of capacity 30 cum/hr - 1 No.")
    assert e["canonical"] == "batching_plant" and e["capacity_unit"] == "cum/hr"
    q = parse_quantity_line("(i) Cement concrete including RCC - 5,000 cum")
    assert q["canonical"] == "concrete" and q["min_quantity"] == "5000"
    assert parse_quantity_line("Batching plant 30 cum/hr") is None  # a capacity, not an executed quantity
    assert min_qualification("B.E./B.Tech or Diploma") == "diploma"
    assert min_qualification("shall be deployed") is None  # the English word "be" is not B.E.
    assert norm_unit("km") == ("m", 1000) and norm_unit("Cu.m")[0] == "cum"
    assert equipment_matches("Hydraulic excavator", "Excavator (Poclain)")
    assert not equipment_matches("Hydraulic excavator", "Backhoe loader (JCB)")
    assert item_matches("Laying of DI pipes", "DI pipe laying DI", "DI")
    assert not item_matches("Laying of DI pipes", "HDPE pipe laying", "DI")
    assert item_matches("Cement concrete including RCC", "R.C.C. work")


def _company(org, key):
    spec = TECH_PARTNERS[key]
    ent = entity(org, spec["legal_name"])
    for cat, title, fields, expiry in spec["records"]:
        evidence(org, ent["id"], cat, title, fields, expiry=expiry)
    return ent


def _tech_tender(org, **kw):
    t = tender(org, **kw)
    upload(org, t["id"], technical_nit_pdf(), "TECH_NIT.pdf")
    set_cutoff(org, t["id"], CUTOFF)
    return t["id"]


def test_rules_proposed_for_technical_criteria_with_jv_suggestions(org):
    a = _company(org, "alpha")
    tid = _tech_tender(org, entity_id=a["id"])
    rules = {r["rule_type"]: r for r in org.get(f"/api/v1/tenders/{tid}/rules")}
    for rt in ("personnel", "equipment", "physical_quantity", "registration_class"):
        assert rt in rules, rt
    assert rules["similar_work"]["params"]["jv_suggested"]["mode"] == "lead_only"
    assert rules["similar_work"]["params"]["similar_definition"].startswith("laying of DI")
    assert rules["avg_turnover"]["params"]["jv_suggested"] == {
        "mode": "aggregate_sum", "lead_min_pct": "60", "member_min_pct": "30", "basis": "JV clause: members' figures are added"}
    assert rules["net_worth"]["params"]["jv_suggested"]["mode"] == "each_member"
    assert "lead_min_pct" not in rules["personnel"]["params"]["jv_suggested"]  # staff are pooled, no % of a person
    tasks = org.get(f"/api/v1/tenders/{tid}/tasks")
    assert any(t["evidence_category"] == "personnel" for t in tasks)
    assert any(t["evidence_category"] == "executed_quantity" for t in tasks)


def _approve(org, tid, use_jv_suggestion=False):
    for r in org.get(f"/api/v1/tenders/{tid}/rules"):
        if r["status"] != "proposed":
            continue
        params = dict(r["params"])
        if use_jv_suggestion and params.get("jv_suggested"):
            params["jv"] = {k: v for k, v in params["jv_suggested"].items() if k != "basis"}
        org.post(f"/api/v1/tenders/{tid}/rules/{r['id']}/approve", json={"params": params, "confirm_low_confidence": True})


def _by_rule(evals):
    out = {}
    for e in evals:
        p = e["rule"]["params"]
        out.setdefault((e["rule"]["rule_type"], p.get("role") or p.get("equipment") or p.get("item") or ""), []).append(e)
    return out


def test_single_company_technical_evaluation_and_no_double_counting(org):
    a = _company(org, "alpha")
    tid = _tech_tender(org, entity_id=a["id"])
    _approve(org, tid)
    ev = _by_rule(org.post(f"/api/v1/tenders/{tid}/evaluate").json()["evaluations"])
    pm = ev[("personnel", "Project Manager")][0]
    assert pm["result"] == "pass" and "R. Menon" in pm["explanation"]
    # Menon is assigned as Project Manager, so he cannot also be one of the two Site Engineers
    se = ev[("personnel", "Site Engineer")][0]
    assert se["result"] == "fail"
    people = {p["person"]: p["result"] for p in se["calculation"]["people"]}
    assert people["R. Menon (SYNTHETIC)"] == "already counted for another position"
    assert ev[("personnel", "Mechanical Engineer")][0]["result"] == "fail"
    assert ev[("equipment", "Crane")][0]["result"] == "pass"                # 12 MT >= 10 MT
    assert ev[("equipment", "Hydraulic excavator")][0]["result"] == "fail"  # 1 of 2
    pipe = ev[("physical_quantity", "Laying of DI pipes")][0]
    assert pipe["result"] == "fail" and pipe["calculation"]["achieved"] == "10000"  # 10 km < 15 km, in metres
    assert ev[("registration_class", "")][0]["result"] == "pass"


def test_planner_finds_the_jv_when_neither_company_qualifies_alone(org):
    a = _company(org, "alpha")
    b = _company(org, "beta")
    tid = _tech_tender(org, entity_id=a["id"])
    plan = org.get(f"/api/v1/tenders/{tid}/qualification-plan")
    singles = [o for o in plan["options"] if o["kind"] == "single"]
    assert singles and all(o["status"] == "does_not_qualify" for o in singles)
    best = plan["options"][0]
    assert best["kind"] == "jv", [(o["title"], o["status"], o["todo"]) for o in plan["options"][:3]]
    assert best["members"][0]["legal_entity_id"] == a["id"] and best["members"][0]["role"] == "lead"
    assert best["members"][0]["share_pct"] == "51" and best["members"][1]["share_pct"] == "49.00"
    assert best["status"] == "qualifies_subject_to_confirmation", best["todo"]  # rules unconfirmed; JV sharing suggested
    assert best["counts"]["fail"] == 0 and best["counts"]["insufficient_evidence"] == 0
    assert "No single company qualifies alone" in plan["headline"]
    # coverage matrix: who meets what on its own
    pm = next(c for c in plan["criteria"] if "Project Manager" in c["text"])
    assert pm["cells"][str(a["id"])]["result"] == "pass" and pm["cells"][str(b["id"])]["result"] == "fail"
    mech = next(c for c in plan["criteria"] if "Mechanical Engineer" in c["text"])
    assert mech["met_alone_by"] == [TECH_PARTNERS["beta"]["legal_name"]]
    # Beta as lead fails the lead-only similar work and the lead 60% minimum
    beta_lead = next(o for o in plan["options"] if o["kind"] == "jv" and o["members"][0]["legal_entity_id"] == b["id"])
    assert beta_lead["status"] == "does_not_qualify"
    # contributions are shown per member for criteria where figures are added
    pipe_key = next(c["key"] for c in plan["criteria"] if "DI pipes" in c["text"])
    pipe_row = next(r for r in best["rows"] if r["criterion"] == pipe_key)
    assert pipe_row["result"] == "pass" and len(pipe_row["contributions"]) == 2


def test_confirmed_jv_rules_make_the_eligibility_matrix_pass_for_the_jv(org):
    a = _company(org, "alpha")
    b = _company(org, "beta")
    jv = org.post("/api/v1/jvs", json={"name": "Alpha-Beta JV (SYNTHETIC)", "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "51"},
        {"legal_entity_id": b["id"], "role": "member", "share_pct": "49"}]}).json()
    tid = _tech_tender(org, jv_id=jv["id"])
    _approve(org, tid, use_jv_suggestion=True)
    res = org.post(f"/api/v1/tenders/{tid}/evaluate").json()
    crit = [x for x in res["readiness"]["blockers"] if x["type"] == "eligibility"]
    assert not crit, crit


def test_jv_member_minimum_blocks_when_partner_contributes_too_little(org):
    a = _company(org, "alpha")
    weak = entity(org, "Gamma Civil Works (SYNTHETIC)")
    for fy in ("2025-26", "2024-25", "2023-24"):
        evidence(org, weak["id"], "turnover_certificate", f"Gamma FY {fy}", {"fy": fy, "turnover": "30000000"})
    jv = org.post("/api/v1/jvs", json={"name": "Alpha-Gamma JV", "members": [
        {"legal_entity_id": a["id"], "role": "lead", "share_pct": "60"},
        {"legal_entity_id": weak["id"], "role": "member", "share_pct": "40"}]}).json()
    tid = _tech_tender(org, jv_id=jv["id"])
    _approve(org, tid, use_jv_suggestion=True)
    ev = [e for e in org.post(f"/api/v1/tenders/{tid}/evaluate").json()["evaluations"] if e["rule"]["rule_type"] == "avg_turnover"]
    by = {e["subject"]: e for e in ev}
    assert by["jv:aggregate"]["result"] == "pass"            # 10 + 3 = 13 Cr >= 12 Cr
    assert by[f"entity:{weak['id']}"]["result"] == "fail"    # 3 Cr < 30% of 12 Cr = 3.6 Cr
