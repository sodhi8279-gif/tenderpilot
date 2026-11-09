"""Acceptance tests mapped to brief section P. All data is SYNTHETIC."""
from __future__ import annotations

import io
import zipfile
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import openpyxl
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ExtractionJob, Requirement, RequirementVersion, SourcePage
from fixtures.synthetic import DEADLINE, EXTENDED
from helpers import (approve_all_rules, boq_xlsx, corrigendum_pdf, entity, evidence, full_evidence, nit_pdf, reqs,
                     set_cutoff, simple_pdf, tender, upload)

CUTOFF = DEADLINE.isoformat()


def _eval(org, tid):
    return org.post(f"/api/v1/tenders/{tid}/evaluate").json()


def _results_by_type(org, tid):
    out = {}
    for e in _eval(org, tid)["evaluations"]:
        out.setdefault(e["rule"]["rule_type"], []).append(e)
    return out


# P1 -------------------------------------------------------------------------------------------
def test_mixed_tender_reports_every_page_and_links_sources(org):
    e = entity(org)
    t = tender(org, e["id"])
    d = upload(org, t["id"], nit_pdf(), "NIT_volume1.pdf")
    pages = org.get(f"/api/v1/tenders/{t['id']}/documents/{d['id']}/pages")
    assert len(pages) == 5
    statuses = [p["status"] for p in pages]
    assert statuses == ["processed", "processed", "processed", "unreadable", "duplicate"]
    assert "OCR" in pages[3]["reason"] and "page 3" in pages[4]["reason"].lower()
    comp = org.get(f"/api/v1/tenders/{t['id']}/completeness")
    assert comp["total_pages"] == 5
    r = reqs(org, t["id"])
    turnover = r["fin.avg_turnover"]["current"]
    assert turnover["value"]["amount"] == "75000000.00" and turnover["value"]["years"] == 3
    a = turnover["anchor"]
    assert a["page_index"] == 1 and a["clause_no"] == "10.1" and "7.50 Crore" in a["excerpt"]
    assert a["bbox"] and a["doc_sha256"] == d["sha256"]
    assert r["commercial.emd"]["current"]["anchor"]["clause_no"] == "3"
    # each similar-work alternative uses its own clause's period (7 years), not the turnover clause's 3 years
    assert {r[f"exp.similar_work.{n}"]["current"]["value"]["period_years"] for n in (1, 2, 3)} == {7}
    assert r["deadline.bid_submission"]["current"]["value"]["date"] == CUTOFF
    img = org.req("GET", f"/api/v1/tenders/{t['id']}/documents/{d['id']}/pages/1/image",
                  params=dict(zip(["x0", "y0", "x1", "y1"], a["bbox"])))
    assert img.headers["content-type"] == "image/png" and len(img.content) > 1000


# P2 -------------------------------------------------------------------------------------------
def test_corrigendum_changes_only_its_clauses_and_invalidates_dependents(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    upload(org, tid, nit_pdf(), "NIT.pdf")
    set_cutoff(org, tid, CUTOFF)
    approve_all_rules(org, tid)
    full_evidence(org, e["id"])
    before = _results_by_type(org, tid)
    assert before["avg_turnover"][0]["result"] == "pass"
    cover = next(x for x in org.get(f"/api/v1/tenders/{tid}/templates")["templates"] if x["builtin_key"] == "cover_letter")
    gen = org.post(f"/api/v1/tenders/{tid}/templates/{cover['id']}/generate").json()
    assert gen["stale"] is False

    # Corrigendum 1: deadline only
    upload(org, tid, corrigendum_pdf(), "Corrigendum_1.pdf")
    r = reqs(org, tid)
    dl = r["deadline.bid_submission"]
    assert len(dl["versions"]) == 2 and dl["current"]["value"]["date"] == EXTENDED.isoformat()
    assert dl["current"]["review_status"] == "needs_review" and dl["has_conflict"] is True
    assert dl["versions"][0]["value"]["date"] == CUTOFF  # old requirement preserved
    assert len(r["commercial.emd"]["versions"]) == 1 and len(r["fin.avg_turnover"]["versions"]) == 1
    assert org.get(f"/api/v1/tenders/{tid}")["current_version"] == 2
    gens = org.get(f"/api/v1/tenders/{tid}/generated")
    assert next(g for g in gens if g["id"] == gen["id"])["stale"] is True
    rules = {x["rule_type"]: x for x in org.get(f"/api/v1/tenders/{tid}/rules")}
    assert rules["avg_turnover"]["status"] == "approved"  # untouched by a deadline-only corrigendum
    blockers = org.get(f"/api/v1/tenders/{tid}")["readiness"]["blockers"]
    assert any(b["type"] == "conflict" for b in blockers)

    # Corrigendum 2 changes the turnover threshold: its rule and evaluation become stale
    c2 = simple_pdf("CORRIGENDUM No. 2", ["10.1 Average annual financial turnover during the last 3 financial years should be "
                                          "at least Rs. 6.00 Crore."])
    upload(org, tid, c2, "Corrigendum_2.pdf", doc_class="corrigendum")
    rules_all = org.get(f"/api/v1/tenders/{tid}/rules", params={"include_history": True})
    old = [x for x in rules_all if x["rule_type"] == "avg_turnover" and x["status"] == "superseded"]
    assert old, "turnover rule must be superseded by the amendment"
    with SessionLocal() as db:
        from app.models import Evaluation
        stale = db.scalars(select(Evaluation).where(Evaluation.rule_id == old[0]["id"])).all()
        assert stale and all(ev.stale for ev in stale)


# P3 -------------------------------------------------------------------------------------------
def test_missing_mandatory_certificate_is_insufficient_and_blocks(org):
    e = entity(org)
    t = tender(org, e["id"])
    upload(org, t["id"], nit_pdf(), "NIT.pdf")
    set_cutoff(org, t["id"], CUTOFF)
    approve_all_rules(org, t["id"])
    res = _results_by_type(org, t["id"])
    iso = next(x for x in res["document_present"] if x["rule"]["params"]["evidence_category"] == "quality_certification")
    assert iso["result"] == "insufficient_evidence"
    t2 = org.get(f"/api/v1/tenders/{t['id']}")
    assert t2["readiness"]["eligible_ready"] is False
    pf = org.post(f"/api/v1/tenders/{t['id']}/preflight").json()
    assert pf["final_ready"] is False


# P4 -------------------------------------------------------------------------------------------
def test_document_valid_today_but_expired_at_cutoff(org):
    e = entity(org)
    t = tender(org, e["id"])
    upload(org, t["id"], nit_pdf(), "NIT.pdf")
    approve_all_rules(org, t["id"])
    expires = (DEADLINE - timedelta(days=5)).isoformat()  # valid today, invalid at cutoff
    evidence(org, e["id"], "quality_certification", "ISO 9001 (expiring)", expiry=expires)
    # No cutoff confirmed yet: never silently use today's date
    res = _results_by_type(org, t["id"])
    iso = next(x for x in res["document_present"] if x["rule"]["params"]["evidence_category"] == "quality_certification")
    assert iso["result"] == "ambiguous" and "cutoff" in iso["explanation"].lower()
    set_cutoff(org, t["id"], CUTOFF)
    res = _results_by_type(org, t["id"])
    iso = next(x for x in res["document_present"] if x["rule"]["params"]["evidence_category"] == "quality_certification")
    assert iso["result"] == "insufficient_evidence" and "before the cutoff" in iso["explanation"]
    evidence(org, e["id"], "quality_certification", "ISO 9001 (renewed)", expiry=(DEADLINE + timedelta(days=300)).isoformat())
    res = _results_by_type(org, t["id"])
    iso = next(x for x in res["document_present"] if x["rule"]["params"]["evidence_category"] == "quality_certification")
    assert iso["result"] == "pass"


# P5 -------------------------------------------------------------------------------------------
def test_jv_individual_lead_and_aggregate_thresholds(org):
    lead = entity(org, "Lead Builders Private Limited (SYNTHETIC)")
    mem = entity(org, "Member Pumps LLP (SYNTHETIC)")
    jv = org.post("/api/v1/jvs", json={"name": "Lead-Member JV", "members": [
        {"legal_entity_id": lead["id"], "role": "lead", "share_pct": "60"},
        {"legal_entity_id": mem["id"], "role": "member", "share_pct": "40"}]}).json()
    t = tender(org, jv_id=jv["id"])
    tid = t["id"]
    upload(org, tid, nit_pdf(), "NIT.pdf")
    set_cutoff(org, tid, CUTOFF)
    fys = ["2025-26", "2024-25", "2023-24"]
    for fy in fys:
        evidence(org, lead["id"], "turnover_certificate", f"Lead FY {fy}", {"fy": fy, "turnover": "50000000"})
        evidence(org, mem["id"], "turnover_certificate", f"Member FY {fy}", {"fy": fy, "turnover": "15000000"})
    # Without an explicit JV rule the engine refuses to assume additivity
    approve_all_rules(org, tid)
    res = _results_by_type(org, tid)
    assert res["avg_turnover"][0]["result"] == "ambiguous"
    assert {x["result"] for x in res["jv_permitted"]} == {"pass"}
    assert {x["result"] for x in res["jv_lead_share"]} == {"pass"}
    assert {x["result"] for x in res["jv_max_members"]} == {"pass"}
    # Configure: aggregate sum, lead >= 60%, each other member >= 25% of the threshold
    rule = next(x for x in org.get(f"/api/v1/tenders/{tid}/rules") if x["rule_type"] == "avg_turnover")
    nr = org.req("PUT", f"/api/v1/tenders/{tid}/rules/{rule['id']}", json={"params": {"jv": {
        "mode": "aggregate_sum", "lead_min_pct": "60", "member_min_pct": "25"}}}).json()
    org.post(f"/api/v1/tenders/{tid}/rules/{nr['id']}/approve", json={"params": None})
    res = {x["subject"]: x for x in _results_by_type(org, tid)["avg_turnover"]}
    assert res[f"entity:{lead['id']}"]["result"] == "pass"           # 5.0 Cr >= 4.5 Cr (60%)
    assert res[f"entity:{mem['id']}"]["result"] == "fail"            # 1.5 Cr < 1.875 Cr (25%)
    assert res["jv:aggregate"]["result"] == "fail"                    # 6.5 Cr < 7.5 Cr
    assert res["jv:aggregate"]["calculation"]["aggregate"] == "65000000.00"
    for fy in fys:
        evidence(org, mem["id"], "turnover_certificate", f"Member FY {fy} revised", {"fy": fy, "turnover": "30000000"},
                 verify=True)
    # replace member's older records: mark old non-current via replacement flow is optional; newest by FY wins only if unique
    with SessionLocal() as db:
        from app.models import EvidenceRecord
        for r in db.scalars(select(EvidenceRecord).where(EvidenceRecord.legal_entity_id == mem["id"],
                                                         EvidenceRecord.title.not_like("%revised%"))):
            r.is_current = False
        db.commit()
    res = {x["subject"]: x for x in _results_by_type(org, tid)["avg_turnover"]}
    assert res[f"entity:{mem['id']}"]["result"] == "pass" and res["jv:aggregate"]["result"] == "pass"


# P6 -------------------------------------------------------------------------------------------
def test_similar_names_cannot_share_evidence_without_attribution(org):
    a = entity(org, "Shree Ganesh Infra Private Limited (SYNTHETIC)")
    b = entity(org, "Shree Ganesh Infra LLP (SYNTHETIC)")
    t = tender(org, b["id"])
    upload(org, t["id"], nit_pdf(), "NIT.pdf")
    set_cutoff(org, t["id"], CUTOFF)
    approve_all_rules(org, t["id"])
    evidence(org, a["id"], "quality_certification", "ISO of A", expiry="2099-01-01")
    iso = lambda: next(x for x in _results_by_type(org, t["id"])["document_present"]  # noqa: E731
                       if x["rule"]["params"]["evidence_category"] == "quality_certification")
    assert iso()["result"] == "insufficient_evidence"  # A's evidence never counts for B
    rec = evidence(org, b["id"], "quality_certification", "ISO filed under B", expiry="2099-01-01",
                   subject_name="Shree Ganesh Infra Pvt Ltd (SYNTHETIC)")
    first = iso()
    assert first["result"] == "insufficient_evidence"
    assert any("approved attribution" in x["reason"] for x in first["calculation"]["excluded_evidence"])
    a_rec = org.get("/api/v1/evidence", params={"entity_id": a["id"]})[0]
    org.post("/api/v1/evidence/attribution", expect=409,
             json={"tender_id": t["id"], "evidence_id": a_rec["id"], "entity_id": b["id"], "note": "attempt to transfer credentials"})
    org.post("/api/v1/evidence/attribution",
             json={"tender_id": t["id"], "evidence_id": rec["id"], "entity_id": b["id"],
                   "note": "Certificate issued before conversion from Pvt Ltd to LLP; conversion order on file"})
    assert iso()["result"] == "pass"


# P7 -------------------------------------------------------------------------------------------
def test_low_confidence_value_cannot_silently_become_rule_or_rate(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    upload(org, tid, nit_pdf(), "NIT.pdf")
    with SessionLocal() as db:
        req = db.scalar(select(Requirement).where(Requirement.tender_id == tid, Requirement.key == "fin.solvency"))
        v = db.scalar(select(RequirementVersion).where(RequirementVersion.requirement_id == req.id))
        v.confidence = 0.55  # as if read by OCR with poor quality
        from app.models import EligibilityRule, Tender
        for r in db.scalars(select(EligibilityRule).where(EligibilityRule.requirement_id == req.id)):
            r.status = "superseded"
        from app.services.eligibility import propose_rules
        propose_rules(db, db.get(Tender, tid))
        db.commit()
    rule = next(x for x in org.get(f"/api/v1/tenders/{tid}/rules") if x["rule_type"] == "solvency")
    assert rule["low_confidence"] is True
    org.post(f"/api/v1/tenders/{tid}/rules/{rule['id']}/approve", expect=409, json={})
    org.post(f"/api/v1/tenders/{tid}/rules/{rule['id']}/approve", expect=409, json={"params": rule["params"]})
    ok = org.post(f"/api/v1/tenders/{tid}/rules/{rule['id']}/approve",
                  json={"params": {"min": "50000000"}, "confirm_low_confidence": True}).json()
    assert ok["status"] == "approved"
    assert any(b["type"] == "low_confidence" for b in org.get(f"/api/v1/tenders/{tid}")["readiness"]["blockers"])

    # BOQ: an assumed/low-confidence rate surfaces as a warning and blocks silent approval
    boq = org.post(f"/api/v1/tenders/{tid}/boqs", files={"file": ("BOQ.xlsx", boq_xlsx())}).json()
    src = org.post(f"/api/v1/tenders/{tid}/rates", json={"resource": "Excavation (OCR'd rate list)", "unit": "cum", "rate": "250",
                                                          "source_type": "assumption", "confidence": 0.4}).json()
    assert src["confidence"] <= 0.5
    for it in boq["items"]:
        org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis",
                json={"components": [{"type": "material", "description": "rate", "qty": "1", "rate_source_id": src["id"]}]})
    q = org.post(f"/api/v1/tenders/{tid}/boqs/{boq['id']}/quotes", json={}).json()
    assert q["snapshot"]["warnings"]
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/estimator-review", "estimator",
                 json={"decision": "approved", "row_version": q["row_version"]}).json()
    org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/approve", "authorized_approver", expect=409,
             json={"decision": "approved", "row_version": q["row_version"]})


# P8 -------------------------------------------------------------------------------------------
RATES = ["250.00", "6543.21", "3120.50", "1250000", "1875.75"]


def test_boq_calculations_rounding_and_margin_reconcile(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    boq = org.post(f"/api/v1/tenders/{tid}/boqs", files={"file": ("BOQ.xlsx", boq_xlsx())}).json()
    assert len(boq["items"]) == 5 and boq["items"][1]["quantity"] == "850.5" and boq["items"][2]["unit"] == "rmt"
    for it, rate in zip(boq["items"], RATES):
        src = org.post(f"/api/v1/tenders/{tid}/rates", json={"resource": it["description"][:40], "unit": it["unit"], "rate": rate,
                                                              "source_type": "quotation", "supplier": "Synthetic Supplier",
                                                              "quote_date": "2026-11-01", "gst_pct": "18"}).json()
        org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis",
                json={"components": [{"type": "material", "description": "supply+lay", "qty": "1", "rate_source_id": src["id"]}]})
    q = org.post(f"/api/v1/tenders/{tid}/boqs/{boq['id']}/quotes", json={}).json()
    qtys = [Decimal("1200"), Decimal("850.5"), Decimal("4200"), Decimal("3"), Decimal("640")]
    expected = sum(((qq * Decimal(r)).quantize(Decimal("0.01"), ROUND_HALF_UP) for qq, r in zip(qtys, RATES)), Decimal(0))
    assert Decimal(q["total"]) == expected == Decimal("23921580.11")
    assert q["total_words"] == "Rupees Two Crore Thirty Nine Lakh Twenty One Thousand Five Hundred Eighty and Eleven Paise Only"
    # markup vs margin-on-sales are different formulas
    it = boq["items"][0]
    a1 = org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis", json={
        "components": [{"type": "labour", "description": "gang", "qty": "2", "rate": "40"},
                       {"type": "material", "description": "consumables", "qty": "1", "rate": "18", "wastage_pct": "11.111111111111"}],
        "overheads": {"site_overhead": "0"}, "margin_pct": "10", "margin_mode": "markup_on_cost"}).json()
    assert a1["computed_rate"] == "110.00"
    a2 = org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis", json={
        "components": [{"type": "labour", "description": "gang", "qty": "2", "rate": "40"},
                       {"type": "material", "description": "consumables", "qty": "1", "rate": "18", "wastage_pct": "11.111111111111"}],
        "margin_pct": "10", "margin_mode": "margin_on_sales"}).json()
    assert a2["computed_rate"] == "111.11"
    # non-recoverable GST becomes cost; recoverable is excluded
    a3 = org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis", json={
        "components": [{"type": "material", "description": "cement", "qty": "1", "rate": "100", "gst_pct": "28", "gst_recoverable": False}]}).json()
    assert a3["computed_rate"] == "128.00"
    # the earlier quote is no longer valid after rates changed
    quotes = org.get(f"/api/v1/tenders/{tid}/quotes")
    assert quotes[0]["status"] == "draft"


# P9 -------------------------------------------------------------------------------------------
def _approved_quote(org, tid, boq):
    for it, rate in zip(boq["items"], RATES):
        src = org.post(f"/api/v1/tenders/{tid}/rates", json={"resource": it["description"][:40], "unit": it["unit"], "rate": rate,
                                                              "source_type": "quotation", "supplier": "Synthetic Supplier",
                                                              "quote_date": "2026-11-01"}).json()
        org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{it['id']}/analysis",
                json={"components": [{"type": "material", "description": "x", "qty": "1", "rate_source_id": src["id"]}]})
    q = org.post(f"/api/v1/tenders/{tid}/boqs/{boq['id']}/quotes", json={}).json()
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/estimator-review", "estimator",
                 json={"decision": "approved", "row_version": q["row_version"]}).json()
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/approve", "authorized_approver",
                 json={"decision": "approved", "row_version": q["row_version"]}).json()
    assert q["status"] == "approved"
    return q


def test_official_workbook_only_entry_cells_change_and_unsupported_fails_safely(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    original = boq_xlsx()
    boq = org.post(f"/api/v1/tenders/{tid}/boqs", files={"file": ("BOQ.xlsx", original)}).json()
    assert boq["roundtrip_supported"] is True
    q = _approved_quote(org, tid, boq)
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/export").json()
    assert q["export_report"]["ok"] is True and q["export_report"]["cells_written"] == 5
    out = org.req("GET", f"/api/v1/tenders/{tid}/quotes/{q['id']}/export").content
    wa, wb = openpyxl.load_workbook(io.BytesIO(original)), openpyxl.load_workbook(io.BytesIO(out))
    assert wa.sheetnames == wb.sheetnames
    assert wb["DeptRef"].sheet_state == "hidden" and wb["BOQ"].protection.sheet is True
    for ws_a, ws_b in zip(wa.worksheets, wb.worksheets):
        for row in ws_a.iter_rows():
            for c in row:
                if ws_a.title == "BOQ" and c.coordinate in {f"E{n}" for n in range(4, 9)}:
                    continue
                assert ws_b[c.coordinate].value == c.value, c.coordinate
    assert wb["BOQ"]["F4"].value == "=C4*E4" and wb["BOQ"]["E5"].value == 6543.21
    # legacy .xls bytes -> refused
    r = org.post(f"/api/v1/tenders/{tid}/boqs", expect=422, files={"file": ("old.xls", b"\xd0\xcf\x11\xe0" + b"\0" * 600)})
    assert "manual" in r.json()["detail"].lower() or "cannot" in r.json()["detail"].lower() or "read" in r.json()["detail"].lower()
    # protected sheet with LOCKED rate cells -> import flags manual entry; export refuses
    import openpyxl as ox
    wbk = ox.load_workbook(io.BytesIO(boq_xlsx()))
    from openpyxl.styles import Protection
    for r_ in range(4, 9):
        wbk["BOQ"][f"E{r_}"].protection = Protection(locked=True)
    buf = io.BytesIO()
    wbk.save(buf)
    t2 = tender(org, e["id"])
    boq2 = org.post(f"/api/v1/tenders/{t2['id']}/boqs", files={"file": ("BOQ_locked.xlsx", buf.getvalue())}).json()
    assert boq2["roundtrip_supported"] is False
    q2 = _approved_quote(org, t2["id"], boq2)
    org.post(f"/api/v1/tenders/{t2['id']}/quotes/{q2['id']}/export", expect=409)


# P10/P11/P12 + full flow --------------------------------------------------------------------
def _ready_tender(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    upload(org, tid, nit_pdf(), "NIT.pdf")
    set_cutoff(org, tid, CUTOFF)
    for r in org.get(f"/api/v1/tenders/{tid}/requirements"):
        cur = r["current"]
        org.post(f"/api/v1/tenders/{tid}/requirements/{r['id']}/review", "technical_reviewer",
                 json={"version_no": cur["version_no"], "decision": "approve"})
    approve_all_rules(org, tid)
    full_evidence(org, e["id"])
    evidence(org, e["id"], "emd_proof", "EMD bank guarantee (SYNTHETIC)")
    evidence(org, e["id"], "fee_proof", "Tender fee receipt (SYNTHETIC)")
    evidence(org, e["id"], "turnover_certificate", "CA turnover summary for technical envelope",
             {"fy": "2022-23", "turnover": "60000000"},
             data=simple_pdf("CA certificate", ["Turnover FY 2025-26: Rs 9,00,00,000", "Turnover FY 2024-25: Rs 8,00,00,000"]))
    ev = _eval(org, tid)
    assert all(x["result"] in ("pass", "not_applicable") or x["rule"]["rule_type"] == "similar_work" for x in ev["evaluations"]), \
        [(x["rule"]["rule_type"], x["result"], x["explanation"]) for x in ev["evaluations"] if x["result"] not in ("pass", "not_applicable")]
    evid = {}
    for rec in org.get("/api/v1/evidence", params={"entity_id": e["id"]}):
        evid.setdefault(rec["category"], rec)
    for task in org.get(f"/api/v1/tenders/{tid}/tasks"):
        if task["evidence_category"]:
            org.req("PATCH", f"/api/v1/tenders/{tid}/tasks/{task['id']}", json={"evidence_id": evid[task["evidence_category"]]["id"]})
        org.req("PATCH", f"/api/v1/tenders/{tid}/tasks/{task['id']}", json={"status": "accepted"})
    return e, tid, evid


def _setup_slots(org, tid, evid, gen_id, quote_id, tech_extra=None, max_mb=None):
    org.req("PUT", f"/api/v1/tenders/{tid}/envelope", json={"envelopes": ["fee_emd", "technical", "financial"],
                                                             "zip_accepted": None, "source_note": "NIT clause 9"})
    org.post(f"/api/v1/tenders/{tid}/slots", json={"envelope": "fee_emd", "name": "EMD proof",
                                                   "assignments": [{"type": "evidence", "id": evid["emd_proof"]["id"]}]})
    tech = [{"type": "generated", "id": gen_id}, {"type": "evidence", "id": evid["turnover_certificate"]["id"]}] + (tech_extra or [])
    org.post(f"/api/v1/tenders/{tid}/slots", json={"envelope": "technical", "name": "Technical bid documents", "max_count": 10,
                                                   "signature_required": True, "assignments": tech, "max_size_mb": max_mb})
    org.post(f"/api/v1/tenders/{tid}/slots", json={"envelope": "financial", "name": "Priced BOQ", "allowed_types": ["xlsx"],
                                                   "assignments": [{"type": "quote_export", "id": quote_id}]})


def test_full_flow_preflight_blocks_until_signed_then_exports_and_freezes(org):
    e, tid, evid = _ready_tender(org)
    tpl = next(x for x in org.get(f"/api/v1/tenders/{tid}/templates")["templates"] if x["builtin_key"] == "cover_letter")
    gen = org.post(f"/api/v1/tenders/{tid}/templates/{tpl['id']}/generate").json()
    assert gen["missing_fields"] == [] and gen["status"] == "draft"
    org.post(f"/api/v1/tenders/{tid}/generated/{gen['id']}/status", expect=409, json={"status": "verified"})
    boq = org.post(f"/api/v1/tenders/{tid}/boqs", files={"file": ("BOQ.xlsx", boq_xlsx())}).json()
    q = _approved_quote(org, tid, boq)
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/export").json()
    # financial letter cannot be placed in the technical envelope
    fin_tpl = next(x for x in org.get(f"/api/v1/tenders/{tid}/templates")["templates"] if x["builtin_key"] == "financial_bid_letter")
    fin = org.post(f"/api/v1/tenders/{tid}/templates/{fin_tpl['id']}/generate").json()
    r = org.post(f"/api/v1/tenders/{tid}/slots", expect=409, json={"envelope": "technical", "name": "x",
                                                                   "assignments": [{"type": "generated", "id": fin["id"]}]})
    assert "financial" in r.json()["detail"]
    _setup_slots(org, tid, evid, gen["id"], q["id"])
    pf = org.post(f"/api/v1/tenders/{tid}/preflight").json()
    failing = {c["id"] for c in pf["checks"] if c["status"] == "fail"}
    assert pf["final_ready"] is False and "signatures" in failing and "files" in failing
    org.post(f"/api/v1/tenders/{tid}/packages", expect=409, json={"final": True})
    draft = org.post(f"/api/v1/tenders/{tid}/packages", json={"final": False}).json()
    assert draft["status"] == "draft" and all("DRAFT" in x["filename"] for x in draft["entries"])
    # signer returns a signed PDF (simulated wet-signed scan)
    signed = simple_pdf("Covering letter - signed copy (SYNTHETIC)", ["Signed: A. Kumar, Director"])
    org.post(f"/api/v1/tenders/{tid}/generated/{gen['id']}/signed", files={"file": ("signed.pdf", signed)})
    pf = org.post(f"/api/v1/tenders/{tid}/preflight").json()
    assert pf["final_ready"] is True, [c for c in pf["checks"] if c["status"] == "fail"]
    price = next(c for c in pf["checks"] if c["id"] == "price_leakage")
    assert price["status"] == "pass"  # turnover figures in technical envelope are allowed
    pkg = org.post(f"/api/v1/tenders/{tid}/packages", json={"final": True}).json()
    assert pkg["status"] == "final_ready" and pkg["revision"] == 2
    tech = [x for x in pkg["entries"] if x["envelope"] == "technical"]
    from app.storage import sha256 as h
    assert any(x["sha256"] == h(signed) for x in tech)  # signed bytes preserved exactly
    org.post(f"/api/v1/tenders/{tid}/packages/{pkg['id']}/freeze", "estimator", expect=403)
    frozen = org.post(f"/api/v1/tenders/{tid}/packages/{pkg['id']}/freeze", "authorized_approver").json()
    assert frozen["status"] == "frozen"
    z = zipfile.ZipFile(io.BytesIO(org.req("GET", f"/api/v1/tenders/{tid}/packages/{pkg['id']}/zip").content))
    names = z.namelist()
    assert any(n.startswith("technical/") for n in names) and any(n.startswith("financial/") for n in names)
    assert not any("manifest" in n.lower() or "internal" in n.lower() for n in names)
    manifest = org.req("GET", f"/api/v1/tenders/{tid}/packages/{pkg['id']}/manifest").json()
    assert manifest["internal_record"] is True and len(manifest["files"]) == len(names)
    rec = org.post(f"/api/v1/tenders/{tid}/packages/{pkg['id']}/receipt",
                   data={"receipt_identifier": "SYN-ACK-0001", "submitted_at": "2026-11-20T14:35:00+05:30"},
                   files={"file": ("ack.pdf", simple_pdf("Portal acknowledgement (SYNTHETIC)"))}).json()
    assert rec["receipt_identifier"] == "SYN-ACK-0001"
    # reload: progress persisted
    t = org.get(f"/api/v1/tenders/{tid}")
    assert t["status"] == "submitted" and t["step_state"]["receipt"]["status"] == "done"
    # later change -> new revision required
    item = boq["items"][0]
    org.req("PUT", f"/api/v1/tenders/{tid}/boq-items/{item['id']}/analysis", json={"direct_rate": "260"})
    assert org.get(f"/api/v1/tenders/{tid}/packages")[0]["status"] == "superseded"


def test_price_leak_in_technical_envelope_and_size_limit_block_final(org):
    e, tid, evid = _ready_tender(org)
    tpl = next(x for x in org.get(f"/api/v1/tenders/{tid}/templates")["templates"] if x["builtin_key"] == "undertaking")
    gen = org.post(f"/api/v1/tenders/{tid}/templates/{tpl['id']}/generate").json()
    org.post(f"/api/v1/tenders/{tid}/generated/{gen['id']}/signed", files={"file": ("s.pdf", simple_pdf("signed undertaking"))})
    boq = org.post(f"/api/v1/tenders/{tid}/boqs", files={"file": ("BOQ.xlsx", boq_xlsx())}).json()
    q = _approved_quote(org, tid, boq)
    q = org.post(f"/api/v1/tenders/{tid}/quotes/{q['id']}/export").json()
    leak = evidence(org, e["id"], "other", "Methodology note", data=simple_pdf("Methodology", ["Our total bid amount is 2,39,21,580.11"]))
    _setup_slots(org, tid, evid, gen["id"], q["id"], tech_extra=[{"type": "evidence", "id": leak["id"]}], max_mb="0.0001")
    pf = org.post(f"/api/v1/tenders/{tid}/preflight").json()
    checks = {c["id"]: c for c in pf["checks"]}
    assert checks["price_leakage"]["status"] == "fail" and "2,39,21,580.11" in " ".join(checks["price_leakage"]["details"])
    assert checks["slots"]["status"] == "fail" and "not auto-compressed" in " ".join(checks["slots"]["details"])
    assert pf["final_ready"] is False


# P13 ------------------------------------------------------------------------------------------
def test_cross_organization_isolation(org, other_org):
    e = entity(org)
    t = tender(org, e["id"])
    d = upload(org, t["id"], nit_pdf(), "NIT.pdf")
    boq = org.post(f"/api/v1/tenders/{t['id']}/boqs", files={"file": ("BOQ.xlsx", boq_xlsx())}).json()
    rec = evidence(org, e["id"], "pan", "PAN")
    tid = t["id"]
    for url in [f"/api/v1/tenders/{tid}", f"/api/v1/tenders/{tid}/requirements", f"/api/v1/tenders/{tid}/documents/{d['id']}/file",
                f"/api/v1/tenders/{tid}/documents/{d['id']}/pages/0/image", f"/api/v1/tenders/{tid}/quotes",
                f"/api/v1/tenders/{tid}/boqs", f"/api/v1/tenders/{tid}/packages", f"/api/v1/evidence/{rec['id']}/file"]:
        r = other_org.req("GET", url, expect=None)
        assert r.status_code == 404, (url, r.status_code)
    assert other_org.get("/api/v1/tenders") == []
    assert all(x["id"] != rec["id"] for x in other_org.get("/api/v1/evidence"))
    other_org.req("PATCH", f"/api/v1/tenders/{tid}", expect=404, json={"row_version": 1, "title": "hijack"})
    # within an org, price data is role-restricted
    org.req("GET", f"/api/v1/tenders/{tid}/quotes", role="document_coordinator", expect=403)
    b = org.get(f"/api/v1/tenders/{tid}/boqs", role="document_coordinator")
    assert b[0]["prices_visible"] is False and "rate" not in b[0]["items"][0]
    assert boq["prices_visible"] is True
    org.req("GET", "/api/v1/tenders", expect=401, headers={"Authorization": "Bearer forged.token.value"}) if False else None
    r = org.client.get("/api/v1/tenders", headers={"Authorization": "Bearer forged.token.value"})
    assert r.status_code == 401


# P14 ------------------------------------------------------------------------------------------
def test_jobs_resume_and_retry_without_duplicates(org):
    e = entity(org)
    t = tender(org, e["id"])
    tid = t["id"]
    nit_bytes = nit_pdf()
    d = org.post(f"/api/v1/tenders/{tid}/documents", files={"file": ("NIT.pdf", nit_bytes)}).json()
    from app.services import jobs
    with SessionLocal() as db:
        job = db.get(ExtractionJob, d["job_id"])
        jobs.claim_next(db)  # simulate a worker that crashed after processing 2 pages
        doc_pages = []
        from app.models import SourceDocument
        from app.services import ingest
        from app.storage import storage
        doc = db.get(SourceDocument, d["id"])
        pdf = ingest.open_pdf(storage.get(doc.storage_key))
        for i in range(pdf.page_count):
            sp = SourcePage(document_id=doc.id, page_index=i, status="pending")
            if i < 2:
                po = ingest.process_pdf_page(pdf, i, ingest.OcrAdapter())
                sp.status, sp.text, sp.text_hash = po.status, po.text, ingest.text_hash(po.text)
            db.add(sp)
            doc_pages.append(sp)
        db.commit()
        from datetime import datetime, timezone
        job = db.get(ExtractionJob, d["job_id"])
        job.locked_at = datetime(2000, 1, 1, tzinfo=timezone.utc)  # stale lock: resumable
        db.commit()
    org.drain()
    with SessionLocal() as db:
        n_pages = db.scalar(select(func.count()).select_from(SourcePage).where(SourcePage.document_id == d["id"]))
        assert n_pages == 5
        n_req = db.scalar(select(func.count()).select_from(Requirement).where(Requirement.tender_id == tid))
    # re-uploading identical bytes and re-enqueueing extraction must not duplicate anything
    again = org.post(f"/api/v1/tenders/{tid}/documents", files={"file": ("copy.pdf", nit_bytes)}).json()
    assert again["duplicate"] is True
    jobs_list = org.get(f"/api/v1/tenders/{tid}/jobs")
    ext = next(j for j in jobs_list if j["kind"] == "extract_requirements")
    with SessionLocal() as db:
        j = db.get(ExtractionJob, ext["id"])
        j.status = "failed"
        db.commit()
    org.post(f"/api/v1/tenders/{tid}/jobs/{ext['id']}/retry")
    org.drain()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Requirement).where(Requirement.tender_id == tid)) == n_req
        assert db.scalar(select(func.count()).select_from(RequirementVersion).join(Requirement).where(Requirement.tender_id == tid)) == n_req
    tasks1 = len(org.get(f"/api/v1/tenders/{tid}/tasks"))
    org.post(f"/api/v1/tenders/{tid}/documents/{d['id']}/classify", json={"doc_class": "nit"})
    org.drain()
    assert len(org.get(f"/api/v1/tenders/{tid}/tasks")) == tasks1
    p1 = org.post(f"/api/v1/tenders/{tid}/packages", json={"final": False}).json()
    p2 = org.post(f"/api/v1/tenders/{tid}/packages", json={"final": False}).json()
    revs = {p["revision"]: p["status"] for p in org.get(f"/api/v1/tenders/{tid}/packages")}
    assert p2["revision"] == p1["revision"] + 1 and revs[p1["revision"]] == "superseded" and revs[p2["revision"]] == "draft"


def test_upload_safety(org):
    t = tender(org, entity(org)["id"])
    tid = t["id"]
    r = org.post(f"/api/v1/tenders/{tid}/documents", expect=415, files={"file": ("evil.exe", b"MZ\x90\x00binary")})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../outside.pdf", nit_pdf())
    d = org.post(f"/api/v1/tenders/{tid}/documents", files={"file": ("pkg.zip", buf.getvalue())}).json()
    org.drain()
    jobs_ = org.get(f"/api/v1/tenders/{tid}/jobs")
    assert any(j["status"] == "failed" and "Unsafe path" in (j["error"] or "") for j in jobs_)
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:
        z.writestr("vol1/NIT.pdf", nit_pdf())
        z.writestr("vol2/BOQ.xlsx", boq_xlsx())
    d2 = org.post(f"/api/v1/tenders/{tid}/documents", files={"file": ("tender.zip", buf2.getvalue())}).json()
    org.drain()
    docs = org.get(f"/api/v1/tenders/{tid}/documents")
    kids = [x for x in docs if x["parent_id"] == d2["id"]]
    assert {k["kind"] for k in kids} == {"pdf", "xlsx"} and all(k["status"] in ("processed", "needs_classification") for k in kids)
    assert d["kind"] == "zip"
