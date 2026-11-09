"""Shared flows for acceptance tests (SYNTHETIC data only)."""
from __future__ import annotations

import json

from fixtures.synthetic import boq_xlsx, corrigendum_pdf, nit_pdf, simple_pdf


def entity(org, name="Demo Infra Projects Private Limited (SYNTHETIC)", signatory="A. Kumar"):
    return org.post("/api/v1/entities", json={"legal_name": name, "registered_address": "1 Demo Road, Demo City",
                                               "signatory_name": signatory, "signatory_designation": "Director",
                                               "pan": "AAAAA0000A", "gstin": "00AAAAA0000A0Z0"}).json()


def tender(org, entity_id=None, jv_id=None):
    body = {"title": "10 MLD WTP at Demo Town (SYNTHETIC)", "tender_ref": "DWW/SYN/2026/017",
            "department": "Department of Water Works, Demo District",
            "bidder_type": "jv" if jv_id else "entity", "bidder_entity_id": entity_id, "jv_id": jv_id}
    return org.post("/api/v1/tenders", json=body).json()


def upload(org, tid, data: bytes, name: str, doc_class="unclassified"):
    r = org.post(f"/api/v1/tenders/{tid}/documents", files={"file": (name, data)}, data={"doc_class": doc_class}).json()
    org.drain()
    return r


def evidence(org, entity_id, category, title, fields=None, expiry=None, subject_name=None, verify=True, data=None):
    files = {"file": (f"{category}.pdf", data or simple_pdf(title, [json.dumps(fields or {})]))}
    form = {"legal_entity_id": str(entity_id), "category": category, "title": title, "fields": json.dumps(fields or {}),
            "issuer": "Synthetic Issuer"}
    if expiry:
        form["expiry_date"] = expiry
    if subject_name:
        form["subject_name_on_document"] = subject_name
    rec = org.post("/api/v1/evidence", files=files, data=form).json()
    if verify:
        org.post(f"/api/v1/evidence/{rec['id']}/verify", json={"state": "verified"})
    return rec


def reqs(org, tid) -> dict:
    return {r["key"]: r for r in org.get(f"/api/v1/tenders/{tid}/requirements")}


def approve_all_rules(org, tid, jv_cfg=None):
    for r in org.get(f"/api/v1/tenders/{tid}/rules"):
        if r["status"] != "proposed":
            continue
        params = None
        if jv_cfg and r["rule_type"] in jv_cfg:
            params = {"jv": jv_cfg[r["rule_type"]]}
        org.post(f"/api/v1/tenders/{tid}/rules/{r['id']}/approve",
                 json={"params": params or r["params"], "confirm_low_confidence": True})


def set_cutoff(org, tid, date="2026-11-25"):
    t = org.get(f"/api/v1/tenders/{tid}")
    org.req("PATCH", f"/api/v1/tenders/{tid}", json={"row_version": t["row_version"], "qualification_cutoff": date,
                                                      "qualification_cutoff_source": "Clause 10.5: as on last date of submission"})


def full_evidence(org, eid, turnover=("90000000", "80000000", "70000000")):
    fys = ["2025-26", "2024-25", "2023-24"]
    for fy, amt in zip(fys, turnover):
        evidence(org, eid, "turnover_certificate", f"CA turnover FY {fy}", {"fy": fy, "turnover": amt})
    for i, (val, dt) in enumerate([("110000000", "2024-03-31"), ("60000000", "2023-08-15"), ("55000000", "2022-01-10")], 1):
        evidence(org, eid, "completion_certificate", f"WTP project {i}", {"work_name": f"Synthetic WTP {i}", "executed_value": val,
                                                                         "completion_date": dt, "status": "completed", "is_similar": True})
    evidence(org, eid, "net_worth", "CA net worth", {"net_worth": "45000000", "as_of": "2026-03-31"})
    evidence(org, eid, "solvency", "Bank solvency", {"amount": "60000000"}, expiry="2027-03-31")
    evidence(org, eid, "pan", "PAN card")
    evidence(org, eid, "gst", "GST registration")
    evidence(org, eid, "quality_certification", "ISO 9001", expiry="2027-06-30")
    evidence(org, eid, "power_of_attorney", "POA for A. Kumar")
    evidence(org, eid, "audited_accounts", "Audited balance sheet 2025-26", {"fy": "2025-26", "turnover": "90000000"})


__all__ = ["entity", "tender", "upload", "evidence", "reqs", "approve_all_rules", "set_cutoff", "full_evidence",
           "nit_pdf", "corrigendum_pdf", "boq_xlsx", "simple_pdf"]
