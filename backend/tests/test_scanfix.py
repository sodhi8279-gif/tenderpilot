"""Vault scan clean-up: rotate / crop / deskew make a cleaned copy; the original never changes. SYNTHETIC."""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services import scanfix
from helpers import entity


def _scan(tilt: float = 3.0) -> bytes:
    img = Image.new("L", (1240, 1754), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for i in range(28):
        d.text((110, 150 + i * 52), f"Line {i + 1}: SYNTHETIC completion certificate text for testing deskew", fill=0, font=font)
    img = img.rotate(tilt, resample=Image.BICUBIC, expand=False, fillcolor=255)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_detects_and_removes_tilt():
    img = scanfix.load_page(_scan(3.0), "image")
    angle = scanfix.detect_skew(img)
    assert -3.5 <= angle <= -2.5
    fixed, done = scanfix.apply_edits(img, deskew=True)
    assert abs(scanfix.detect_skew(fixed)) <= 0.5 and done["deskew_degrees"] == angle


def test_straight_page_is_left_alone():
    assert scanfix.detect_skew(scanfix.load_page(_scan(0.0), "image")) == 0.0


def test_rotate_and_crop():
    img = scanfix.load_page(_scan(0.0), "image")
    out, done = scanfix.apply_edits(img, rotate=90, crop=[0, 0, 0.5, 1])
    assert out.size == (877, 1240) and done["rotate"] == 90
    with pytest.raises(scanfix.ScanFixError):
        scanfix.apply_edits(img, rotate=45)
    with pytest.raises(scanfix.ScanFixError):
        scanfix.apply_edits(img, crop=[0.6, 0, 0.5, 1])


def test_cleaned_copy_keeps_the_original_untouched(org):
    e = entity(org)
    raw = _scan(3.0)
    rec = org.post("/api/v1/evidence", files={"file": ("cert.png", raw)},
                   data={"legal_entity_id": str(e["id"]), "category": "completion_certificate", "title": "Scanned certificate",
                         "fields": json.dumps({"value": "50000000"})}).json()
    org.post(f"/api/v1/evidence/{rec['id']}/verify", json={"state": "verified"})
    info = org.get(f"/api/v1/evidence/{rec['id']}/scan")
    assert info["kind"] == "image" and info["pages"] == 1 and info["cleaned"] == []
    prev = org.post(f"/api/v1/evidence/{rec['id']}/scan/preview", json={"deskew": True})
    assert prev.headers["content-type"] == "image/png" and float(prev.headers["x-deskew-degrees"]) < -2
    d = org.post(f"/api/v1/evidence/{rec['id']}/scan/clean", json={"deskew": True, "crop": [0.02, 0.02, 0.98, 0.98]},
                 role="document_coordinator").json()
    pdf = org.req("GET", f"/api/v1/evidence/cleaned/{d['id']}/file").content
    assert pdf[:4] == b"%PDF" and hashlib.sha256(pdf).hexdigest() == d["sha256"]
    # Original: same bytes, same hash, still verified and current, still version 1.
    assert org.req("GET", f"/api/v1/evidence/{rec['id']}/file").content == raw
    after = next(x for x in org.get("/api/v1/evidence") if x["id"] == rec["id"])
    assert after["sha256"] == hashlib.sha256(raw).hexdigest() and after["verification_state"] == "verified"
    assert after["is_current"] and after["version"] == 1
    assert len(org.get(f"/api/v1/evidence/{rec['id']}/scan")["cleaned"]) == 1
    # Read-only roles cannot save copies; other organizations cannot see them.
    org.post(f"/api/v1/evidence/{rec['id']}/scan/clean", json={"rotate": 90}, role="auditor", expect=403)


def test_other_org_cannot_reach_cleaned_copies(org, other_org):
    e = entity(org)
    rec = org.post("/api/v1/evidence", files={"file": ("c.png", _scan(0.0))},
                   data={"legal_entity_id": str(e["id"]), "category": "completion_certificate", "title": "C"}).json()
    d = org.post(f"/api/v1/evidence/{rec['id']}/scan/clean", json={"rotate": 180}).json()
    other_org.req("GET", f"/api/v1/evidence/cleaned/{d['id']}/file", expect=404)
    other_org.req("GET", f"/api/v1/evidence/{rec['id']}/scan", expect=404)
