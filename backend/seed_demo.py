"""Create a SYNTHETIC demo workspace. Run from backend/:  python seed_demo.py

Creates one demo organization, one user per role (password printed once), a fictional company
with verified sample credentials, and a tender whose synthetic NIT is processed by the real pipeline.
Nothing here is a real tender, company or certificate.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("RUN_WORKER", "0")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Membership, Organization, User  # noqa: E402
from app.security import create_token, hash_password  # noqa: E402
from app.services import jobs  # noqa: E402
from fixtures.synthetic import DEADLINE, TECH_PARTNERS, boq_xlsx, nit_pdf, simple_pdf, technical_nit_pdf  # noqa: E402

ROLES = ["org_admin", "tender_manager", "technical_reviewer", "estimator", "document_coordinator",
         "finance_reviewer", "authorized_approver", "auditor"]
ORG_NAME = "Demo Infra Group (SYNTHETIC)"


def main() -> None:
    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
    with TestClient(app) as client, SessionLocal() as db:
        if db.scalar(select(Organization).where(Organization.name == ORG_NAME)):
            print("Demo organization already exists; nothing changed.")
            return
        org = Organization(name=ORG_NAME, profile={  # the bid-submission company; bidders are its client firms
            "address": "5 Demo Business Park, Demo City", "phone": "0000-111111", "email": "bids@demo-bid-desk.example",
            "contact_person": "Bid Manager (SYNTHETIC)"})
        db.add(org)
        db.flush()
        tokens = {}
        for role in ROLES:
            u = User(email=f"{role.replace('_', '.')}@demo.tenderpilot.local", name=role.replace("_", " ").title(),
                     password_hash=hash_password(password))
            db.add(u)
            db.flush()
            db.add(Membership(org_id=org.id, user_id=u.id, role=role))
            tokens[role] = create_token(u.id, org.id)
        db.commit()
        h = {"Authorization": f"Bearer {tokens['org_admin']}"}

        def post(url, **kw):
            r = client.post(url, headers=h, **kw)
            r.raise_for_status()
            return r.json()

        ent = post("/api/v1/entities", json={"legal_name": "Demo Infra Projects Private Limited (SYNTHETIC)",
                                             "registered_address": "1 Demo Road, Demo City", "signatory_name": "A. Kumar",
                                             "signatory_designation": "Director", "pan": "AAACD0000A", "gstin": "00AAACD0000A1Z0",
                                             "profile": {"cin": "U00000XX2010PTC000000", "udyam": "UDYAM-XX-00-0000000",
                                                         "email": "tenders@demoinfra.example", "phone": "0000-000000",
                                                         "contact_person": "R. Sharma", "bank_name": "Demo Bank (SYNTHETIC)",
                                                         "bank_account": "000000000000", "bank_ifsc": "DEMO0000001"}})

        def ev(cat, title, fields=None, expiry=None):
            data = {"legal_entity_id": str(ent["id"]), "category": cat, "title": title,
                    "fields": json.dumps(fields or {}), "issuer": "Synthetic issuer"}
            if expiry:
                data["expiry_date"] = expiry
            rec = post("/api/v1/evidence", files={"file": (f"{cat}.pdf", simple_pdf(title, [json.dumps(fields or {})]))}, data=data)
            post(f"/api/v1/evidence/{rec['id']}/verify", json={"state": "verified"})

        for fy, amt in (("2025-26", "90000000"), ("2024-25", "80000000"), ("2023-24", "70000000")):
            ev("turnover_certificate", f"CA turnover certificate FY {fy}", {"fy": fy, "turnover": amt})
        for i, (val, dt) in enumerate((("110000000", "2024-03-31"), ("60000000", "2023-08-15"), ("55000000", "2022-01-10")), 1):
            ev("completion_certificate", f"Completion certificate: Synthetic WTP {i}",
               {"work_name": f"Synthetic WTP {i}", "executed_value": val, "completion_date": dt, "status": "completed", "is_similar": True})
        ev("net_worth", "CA net worth certificate", {"net_worth": "45000000", "as_of": "2026-03-31"})
        ev("solvency", "Bank solvency certificate", {"amount": "60000000"}, expiry="2027-03-31")
        ev("pan", "PAN card")
        ev("gst", "GST registration certificate")
        ev("power_of_attorney", "Power of attorney: A. Kumar")
        # The ISO 9001 certificate is deliberately missing so the demo shows a real blocker.
        t = post("/api/v1/tenders", json={"title": "10 MLD WTP at Demo Town (SYNTHETIC)", "tender_ref": "DWW/SYN/2026/017",
                                          "department": "Department of Water Works, Demo District", "bidder_entity_id": ent["id"]})
        post(f"/api/v1/tenders/{t['id']}/documents", files={"file": ("NIT_DWW_SYN_2026_017.pdf", nit_pdf())})
        jobs.run_pending()
        tv = client.get(f"/api/v1/tenders/{t['id']}", headers=h).json()
        client.patch(f"/api/v1/tenders/{t['id']}", headers=h, json={
            "row_version": tv["row_version"], "qualification_cutoff": DEADLINE.isoformat(),
            "qualification_cutoff_source": "Clause 10.5: as on the last date of submission of bids"}).raise_for_status()
        post(f"/api/v1/tenders/{t['id']}/boqs", files={"file": ("BOQ_DWW_SYN_2026_017.xlsx", boq_xlsx())})

        # Second tender: technical criteria (personnel, machinery, quantities) that only a JV of two fictional
        # companies can meet. Shows the "How to qualify" planner.
        partner_ids = {}
        for key, spec in TECH_PARTNERS.items():
            n = len(partner_ids) + 1
            pe = post("/api/v1/entities", json={"legal_name": spec["legal_name"], "registered_address": "Demo Industrial Area",
                                                "signatory_name": "B. Singh", "signatory_designation": "Partner",
                                                "pan": f"AAAFP000{n}B", "gstin": f"00AAAFP000{n}B1Z{n}",
                                                "profile": {"email": f"bids{n}@partner.example", "phone": f"0000-00000{n}"}})
            partner_ids[key] = pe["id"]
            for cat, title, fields, expiry in spec["records"]:
                data = {"legal_entity_id": str(pe["id"]), "category": cat, "title": title, "fields": json.dumps(fields),
                        "issuer": "Synthetic issuer"}
                if expiry:
                    data["expiry_date"] = expiry
                rec = post("/api/v1/evidence", files={"file": (f"{cat}.pdf", simple_pdf(title, [json.dumps(fields)]))}, data=data)
                post(f"/api/v1/evidence/{rec['id']}/verify", json={"state": "verified"})
        t2 = post("/api/v1/tenders", json={"title": "600 mm DI pumping main and pump house, Demo City (SYNTHETIC)",
                                           "tender_ref": "DCWSB/SYN/2026/044", "department": "Demo City Water Supply Board",
                                           "bidder_entity_id": partner_ids["alpha"]})
        post(f"/api/v1/tenders/{t2['id']}/documents", files={"file": ("NIT_DCWSB_SYN_2026_044.pdf", technical_nit_pdf())})
        jobs.run_pending()
        tv = client.get(f"/api/v1/tenders/{t2['id']}", headers=h).json()
        client.patch(f"/api/v1/tenders/{t2['id']}", headers=h, json={
            "row_version": tv["row_version"], "qualification_cutoff": DEADLINE.isoformat(),
            "qualification_cutoff_source": "Clause 8.8: as on the last date of submission of bids"}).raise_for_status()
    print("Demo workspace created (SYNTHETIC data).")
    print(f"Password for every demo user: {password}")
    for role in ROLES:
        print(f"  {role.replace('_', '.')}@demo.tenderpilot.local")


if __name__ == "__main__":
    main()
