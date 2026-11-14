"""Browser journey through the whole app (brief §P15), with the SYNTHETIC demo workspace.

Off by default because it needs a browser. Run it with:
    E2E=1 python -m pytest -q tests/test_e2e_journey.py            (Windows PowerShell: $env:E2E=1; ...)
Needs: pip install playwright, then either `python -m playwright install chromium` or an installed Chrome
(set CHROME_PATH to its executable if Playwright cannot find it). The frontend must be built (npm run build).
Screenshots of each screen are written to E2E_SCREENSHOTS (default: a temporary folder, printed at the end).

It starts its own copy of TenderPilot on a free port with a throw-away data folder; real data is never touched.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("E2E") != "1", reason="browser journey: set E2E=1 to run")

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "e2e-demo-password-123"
ADMIN = "org.admin@demo.tenderpilot.local"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    if not (BACKEND.parent / "frontend" / "dist" / "index.html").exists():
        pytest.skip("Build the frontend first: cd frontend; npm run build")
    data = Path(tempfile.mkdtemp(prefix="tp-e2e-"))
    env = {**os.environ, "DATA_DIR": str(data), "DATABASE_URL": f"sqlite:///{(data / 'tp.db').as_posix()}",
           "STORAGE_DIR": str(data / "storage"), "DEMO_PASSWORD": PASSWORD, "SECRET_KEY": "e2e-secret-key-0123456789abcdef"}
    env.pop("RUN_WORKER", None)
    subprocess.run([sys.executable, "seed_demo.py"], cwd=BACKEND, env={**env, "RUN_WORKER": "0"}, check=True,
                   capture_output=True, timeout=300)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{base}/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("TenderPilot did not start")
    yield base
    proc.terminate()
    proc.wait(10)


def _prepare(base: str) -> int:
    """API set-up the screens then show: a priced and frozen quote, a GCC and an SCC that disagree on EMD."""
    sys.path.insert(0, str(BACKEND))
    from fixtures.synthetic import simple_pdf

    tok = httpx.post(f"{base}/api/v1/auth/login", json={"email": ADMIN, "password": PASSWORD}).json()["token"]
    c = httpx.Client(base_url=f"{base}/api/v1", headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    tid = next(t["id"] for t in c.get("/tenders").json() if "WTP" in t["title"])
    boq = c.get(f"/tenders/{tid}/boqs").json()[0]
    for it in boq["items"]:
        src = c.post(f"/tenders/{tid}/rates", json={"resource": it["description"][:40], "unit": it["unit"], "rate": "1000",
                                                    "source_type": "quotation", "supplier": "Synthetic Supplier",
                                                    "quote_date": "2026-11-01", "gst_pct": "18"}).json()
        c.put(f"/tenders/{tid}/boq-items/{it['id']}/analysis",
              json={"components": [{"type": "material", "description": "supply", "qty": "1", "rate_source_id": src["id"]}]})
    assert c.post(f"/tenders/{tid}/boqs/{boq['id']}/quotes", json={}).status_code == 200
    gcc = simple_pdf("GENERAL CONDITIONS OF CONTRACT (SYNTHETIC)", [
        "4. Earnest Money Deposit (EMD): Rs. 10,00,000 by online payment.",
        "5. Order of Precedence: (a) Special Conditions of Contract; (b) General Conditions of Contract; (c) Drawings."])
    for name, cls, data in (("GCC.pdf", "gcc", gcc), ("SCC.pdf", "scc", simple_pdf("SPECIAL CONDITIONS (SYNTHETIC)", [
            "2. Earnest Money Deposit (EMD): Rs. 12,50,000 by online payment."]))):
        c.post(f"/tenders/{tid}/documents", files={"file": (name, data)}, data={"doc_class": cls})
    for _ in range(60):  # wait for the worker to read them
        if all(j["status"] in ("succeeded", "failed") for j in c.get(f"/tenders/{tid}/jobs").json()):
            break
        time.sleep(1)
    return tid


def test_full_journey(server):
    pw = pytest.importorskip("playwright.sync_api")
    tid = _prepare(server)
    shots = Path(os.environ.get("E2E_SCREENSHOTS") or tempfile.mkdtemp(prefix="tp-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    launch = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(**launch)
        except Exception:  # noqa: BLE001  fall back to an installed Chrome
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        snap = lambda name: page.screenshot(path=str(shots / f"{name}.png"), full_page=True)  # noqa: E731
        expect = pw.expect

        # sign in
        page.goto(f"{server}/login")
        page.get_by_label("Email").fill(ADMIN)
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("heading", name="Tenders")).to_be_visible()
        snap("01-tenders")

        # tender: command centre and every tab
        page.get_by_role("link", name="10 MLD WTP at Demo Town (SYNTHETIC)").click()
        expect(page.get_by_role("tab", name="Command centre")).to_be_visible()
        snap("02-command-centre")

        page.get_by_role("tab", name="Documents").click()
        expect(page.get_by_role("heading", name="Document register")).to_be_visible()
        expect(page.get_by_role("heading", name="Order of precedence")).to_be_visible()
        page.get_by_role("button", name="Use suggested ranks (2)").click()
        expect(page.get_by_label("Rank of SCC.pdf")).to_have_value("1")
        # The NIT also states an EMD but is not named in the clause: the reviewer ranks it, then resolves.
        page.get_by_label("Rank of NIT_DWW_SYN_2026_017.pdf").fill("3")
        page.get_by_role("button", name="Save ranks").click()
        page.get_by_role("button", name=re.compile(r"Resolve \d+ conflict")).click()
        expect(page.get_by_role("button", name=re.compile(r"Resolve \d+ conflict"))).to_have_count(0)
        expect(page.get_by_text("Clause order: SCC → GCC")).to_be_visible()
        snap("03-documents-precedence")

        page.get_by_role("tab", name="Clauses").click()
        page.locator("ul button").first.click()
        expect(page.get_by_text("Review this version")).to_be_visible()
        snap("04-clauses")

        for tab, check in (("How to qualify", None), ("Eligibility", None), ("Checklist", None)):
            page.get_by_role("tab", name=tab).click()
            page.wait_for_load_state("networkidle")
            snap(f"05-{tab.lower().replace(' ', '-')}")

        # document studio: a scanned PDF form, boxes placed by clicking
        from fixtures.synthetic import simple_pdf
        page.get_by_role("tab", name="Document studio").click()
        form = Path(tempfile.mkdtemp()) / "Affidavit.pdf"
        form.write_bytes(simple_pdf("AFFIDAVIT FORMAT (SYNTHETIC)", ["Name of bidder: ________________________"]))
        page.locator("input[type=file][accept='.docx,.pdf']").set_input_files(str(form))
        img = page.get_by_alt_text("Page 1 of the form. Click to place a box.")
        expect(img).to_be_visible()
        img.click(position={"x": 260, "y": 130})
        page.get_by_role("button", name="Save boxes").click()
        expect(page.get_by_text("Field mapping: confirm every field before generating")).to_be_visible()
        page.get_by_role("button", name="Confirm mapping").click()
        page.get_by_role("row", name="Affidavit").get_by_role("button", name="Generate").click()
        expect(page.get_by_role("row", name="Affidavit r1")).to_be_visible()
        snap("06-studio-pdf-form")

        # BOQ and quote: editable scenarios
        page.get_by_role("tab", name="BOQ and quote").click()
        page.get_by_role("button", name="Scenarios").click()
        page.get_by_label("Name of scenario 1").fill("Cement +12%")
        page.get_by_role("button", name="Add scenario").click()
        page.get_by_role("button", name="Calculate").click()
        expect(page.get_by_role("cell", name="Cement +12%")).to_be_visible()
        snap("07-scenarios")

        page.get_by_role("tab", name="Envelopes and export").click()
        page.wait_for_load_state("networkidle")
        snap("08-envelopes")

        # vault: clean up a scan
        page.get_by_role("link", name="Company vault").click()
        page.get_by_role("button", name="Clean up scan").first.click()
        expect(page.get_by_alt_text("Preview of the cleaned page")).to_be_visible()
        page.get_by_role("button", name="Rotate right").click()
        page.get_by_role("button", name="Save cleaned copy (PDF)").click()
        expect(page.get_by_text("Cleaned copies")).to_be_visible()
        snap("09-vault-scan")

        # Hindi labels
        page.get_by_role("button", name="हिन्दी").click()
        expect(page.get_by_role("link", name="निविदाएँ")).to_be_visible()
        page.get_by_role("link", name="निविदाएँ").click()
        expect(page.get_by_role("heading", name="निविदाएँ")).to_be_visible()
        snap("10-hindi")
        page.get_by_role("button", name="English").click()

        browser.close()
    print(f"\nScreenshots: {shots}")
    assert not errors, f"Errors in the page: {errors}"
