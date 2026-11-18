"""Reading client firm documents: the kind of document, its values and the firm details it carries (synthetic)."""
from __future__ import annotations

import io

import fitz
import pytest

from app.services import firmdocs
from app.services.ingest import TesseractAdapter


def pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


GST = """Government of India
Form GST REG-06
[See Rule 10(1)]
Registration Certificate
Registration Number : 01AABCS1234F1Z5
1. Legal Name : SODHI INFRA PROJECTS PRIVATE LIMITED
2. Trade Name, if any : SODHI INFRA
3. Constitution of Business : Private Limited Company
4. Address of Principal Place of Business : 12, Residency Road,
Srinagar, Jammu and Kashmir, 190001
5. Date of Liability : 01/07/2017
6. Date of Validity From : 01/07/2017 To : Not Applicable
Date of issue of Certificate : 15/03/2019
"""

COI = """GOVERNMENT OF INDIA
MINISTRY OF CORPORATE AFFAIRS
Registrar of Companies, Delhi
Certificate of Incorporation
I hereby certify that SODHI INFRA PROJECTS PRIVATE LIMITED is incorporated on this Twelfth day of March
two thousand twelve under the Companies Act, 1956 and that the company is limited by shares.
Corporate Identity Number : U45200DL2012PTC234567
Date of Incorporation : 12/03/2012
"""

UDYAM = """UDYAM REGISTRATION CERTIFICATE
UDYAM REGISTRATION NUMBER UDYAM-JK-01-0012345
NAME OF ENTERPRISE : SODHI INFRA PROJECTS PRIVATE LIMITED
TYPE OF ENTERPRISE : Small
Official address of Enterprise : 12, Residency Road, Srinagar, J&K 190001
Mobile : 9419012345   Email : accounts@sodhiinfra.example
Date of Incorporation : 12/03/2012
Date of Udyam Registration : 05/08/2020
"""

COMPLETION = """PUBLIC HEALTH ENGINEERING DEPARTMENT
Office of the Executive Engineer, Hydraulic Division Srinagar
No. PHE/HDS/2024/1187                                    Dated: 18/04/2024
COMPLETION CERTIFICATE
Certified that M/s Sodhi Infra Projects Pvt Ltd has successfully completed the work.
Name of Work : Augmentation of water supply scheme Pampore including laying of DI pipes 300 mm dia
Agreement No. : 14/EE/HDS of 2021-22
Contract value : Rs. 12.40 Crore
Date of start : 01/06/2021
Date of actual completion : 31/12/2023
Gross value of work done : Rs. 11,85,40,000
The work has been executed to our satisfaction.
Executive Engineer
"""

TURNOVER = """SHARMA & ASSOCIATES
Chartered Accountants
TO WHOM IT MAY CONCERN
This is to certify that the annual turnover of M/s Sodhi Infra Projects Pvt Ltd (PAN AABCS1234F) as per audited
accounts for the last three financial years is as under:
(Rs. in Lakhs)
Financial Year    Turnover
2022-23           1,845.20
2023-24           2,310.75
2024-25           2,904.10
UDIN: 25098765BMIXYZ1234
Date : 20/09/2025
"""

CHEQUE = """STATE BANK OF INDIA
Residency Road Branch, Srinagar
IFSC: SBIN0001234
Pay ____________________ CANCELLED
A/c No. 3012 3456 7890
SODHI INFRA PROJECTS PRIVATE LIMITED
"""


def test_gst_certificate():
    r = firmdocs.read_document(pdf(GST), "gst.pdf")
    assert r["category"] == "gst" and r["category_sure"] and r["title"] == "GST registration certificate"
    f = {k: v["value"] for k, v in r["firm"].items()}
    assert f["gstin"] == "01AABCS1234F1Z5" and f["pan"] == "AABCS1234F"
    assert f["legal_name"] == "SODHI INFRA PROJECTS PRIVATE LIMITED" and f["entity_type"] == "company"
    assert "Residency Road" in f["registered_address"] and "190001" in f["registered_address"]
    assert r["issue_date"] == "2019-03-15" and r["expiry_date"] is None  # "Validity From 01/07/2017 To Not Applicable"
    assert all(v["excerpt"] for v in r["firm"].values())  # every suggestion shows where it came from


def test_certificate_of_incorporation():
    r = firmdocs.read_document(pdf(COI), "coi.pdf")
    assert r["category"] == "incorporation" and r["issuer"].startswith("Registrar of Companies")
    f = {k: v["value"] for k, v in r["firm"].items()}
    assert f["cin"] == "U45200DL2012PTC234567" and f["incorporation_date"] == "2012-03-12"
    assert f["legal_name"] == "SODHI INFRA PROJECTS PRIVATE LIMITED"


def test_udyam_certificate():
    r = firmdocs.read_document(pdf(UDYAM), "udyam.pdf")
    assert r["category"] == "registration" and r["title"] == "Udyam (MSME) registration"
    f = {k: v["value"] for k, v in r["firm"].items()}
    assert f["udyam"] == "UDYAM-JK-01-0012345" and f["msme_category"] == "Small"
    assert f["email"] == "accounts@sodhiinfra.example" and f["phone"] == "9419012345"
    assert r["fields"]["registration_no"].startswith("UDYAM-JK-01-0012345")


def test_completion_certificate():
    r = firmdocs.read_document(pdf(COMPLETION), "cc.pdf")
    assert r["category"] == "completion_certificate"
    f = r["fields"]
    assert f["work_name"].startswith("Augmentation of water supply scheme Pampore")
    assert f["executed_value"] == "118540000" and f["value"] == "124000000"
    assert f["completion_date"] == "2023-12-31" and f["start_date"] == "2021-06-01" and f["status"] == "completed"
    assert r["issuer"] == "PUBLIC HEALTH ENGINEERING DEPARTMENT" and f["employer"] == r["issuer"]
    assert r["subject_name_on_document"] == "Sodhi Infra Projects Pvt Ltd"
    assert r["issue_date"] == "2024-04-18"
    assert "email" not in r["firm"]  # contact details on a department letter are not the firm's


def test_ca_turnover_certificate_in_lakhs():
    r = firmdocs.read_document(pdf(TURNOVER), "turnover.pdf")
    assert r["category"] == "turnover_certificate"
    assert [x["fy"] for x in r["rows"]] == ["2024-25", "2023-24", "2022-23"]
    assert r["rows"][0]["turnover"] == "290410000" and r["rows"][2]["turnover"] == "184520000"
    assert r["fields"]["fy"] == "2024-25" and any("lakhs" in n for n in r["notes"])
    assert r["firm"]["pan"]["value"] == "AABCS1234F"
    assert r["subject_name_on_document"] == "Sodhi Infra Projects Pvt Ltd"
    assert r["rows"][0]["excerpt"] == "2024-25 2,904.10"


def test_cancelled_cheque_gives_bank_details():
    r = firmdocs.read_document(pdf(CHEQUE), "cheque.pdf")
    f = {k: v["value"] for k, v in r["firm"].items()}
    assert f["bank_ifsc"] == "SBIN0001234" and f["bank_account"] == "301234567890" and f["bank_name"] == "STATE BANK OF INDIA"


@pytest.mark.skipif(not TesseractAdapter().available(), reason="Tesseract not installed")
def test_scanned_photo_is_read_with_ocr():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1700, 700), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(["Form GST REG-06", "Registration Certificate", "Registration Number : 01AABCS1234F1Z5",
                              "Legal Name : SODHI INFRA PROJECTS PRIVATE LIMITED"]):
        d.text((60, 60 + i * 70), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = firmdocs.read_document(buf.getvalue(), "photo.png", ocr=TesseractAdapter())
    assert r["read_by"] == "scanned (OCR)" and r["category"] == "gst"
    assert r["firm"]["gstin"]["value"] == "01AABCS1234F1Z5"
    assert any("check every value" in n for n in r["notes"])


def test_unreadable_file_says_so():
    r = firmdocs.read_document(b"just some text", "notes.txt")
    assert not r["readable"] and r["category"] == "other" and r["firm"] == {}


def test_read_endpoint_saves_nothing_and_compares_with_the_firm(org):
    ent = org.post("/api/v1/entities", json={"legal_name": "Sodhi Infra Projects Pvt Ltd", "entity_type": "company",
                                             "registered_address": "Old address", "pan": "AABCS1234F"}).json()
    before = org.get(f"/api/v1/evidence?entity_id={ent['id']}")
    r = org.req("POST", "/api/v1/evidence/read", data={"legal_entity_id": str(ent["id"])},
                files={"file": ("gst.pdf", pdf(GST), "application/pdf")}).json()
    assert r["firm"]["pan"]["state"] == "same"                         # already recorded, identical
    assert r["firm"]["legal_name"]["state"] == "same"                  # "Private Limited" = "Pvt Ltd"
    assert r["firm"]["gstin"]["state"] == "new"                        # empty on the firm
    assert r["firm"]["registered_address"]["state"] == "different"     # never overwritten unless ticked
    assert org.get(f"/api/v1/evidence?entity_id={ent['id']}") == before
    assert org.get(f"/api/v1/entities/{ent['id']}?reveal=true")["registered_address"] == "Old address"
    org.req("POST", "/api/v1/evidence/read", files={"file": ("gst.pdf", pdf(GST), "application/pdf")}, role="auditor", expect=403)


def test_scan_misreadings_of_gstin_are_repaired_only_when_the_check_digit_agrees():
    assert firmdocs.gstin_check_ok("01AABCS1234F1Z5") and not firmdocs.gstin_check_ok("01AABCS1234F1Z6")
    g = firmdocs.find_gstin("GSTIN : OLAABCS1234F1Z5")
    assert g[0] == "01AABCS1234F1Z5" and "corrected" in g[3]
    assert firmdocs.find_gstin("GSTIN : OLAABCS1234F1Z6") is None          # repair would not give a valid number
    g = firmdocs.find_gstin("GSTIN : 01AABCS1234F1Z6")                      # printed exactly but check digit wrong
    assert g[0] == "01AABCS1234F1Z6" and "check digit" in g[3]


def test_iso_certificate_validity():
    r = firmdocs.read_document(pdf("""BUREAU CERTIFICATION SERVICES
This is to certify that the Quality Management System of M/s Sodhi Infra Projects Pvt Ltd
has been assessed and found to conform to ISO 9001:2015
Certificate No. QMS/2024/7781
Date of Issue : 10/02/2024
Valid up to : 09/02/2027
"""), "iso.pdf")
    assert r["category"] == "quality_certification" and r["title"] == "ISO 9001:2015 certificate"
    assert r["issue_date"] == "2024-02-10" and r["expiry_date"] == "2027-02-09"
