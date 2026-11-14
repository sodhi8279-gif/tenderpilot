"""Real dependency checks for Settings: each result says what was tested, what happened, and what to do.

Statuses: ok / warning / error / off (deliberately disabled). Slow self-tests (OCR, LibreOffice) run on
demand and are cached for 10 minutes.
"""
from __future__ import annotations

import io
import shutil
import threading
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import BACKUP_DIR, FRONTEND_DIST, settings
from ..models import ExtractionJob, Organization
from . import ai, convert, ingest

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()
TTL = 600


def _check(cid: str, label: str, status: str, detail: str, action: str | None = None, **extra) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "action": action, **extra}


def _cached(cid: str, refresh: bool, fn) -> dict:
    with _LOCK:
        hit = _CACHE.get(cid)
        if hit and not refresh and time.time() - hit[0] < TTL:
            return {**hit[1], "cached": True}
    res = fn()
    res["checked_at"] = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        _CACHE[cid] = (time.time(), res)
    return res


# ------------------------------------------------------------------ individual checks
def check_database(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        applied = [r[0] for r in db.execute(text("SELECT id FROM schema_version ORDER BY id"))]
    except Exception as exc:  # noqa: BLE001
        return _check("database", "Database", "error", f"Query failed ({type(exc).__name__}).",
                      "Check DATABASE_URL and that the database server is running.")
    kind = settings.database_url.split(":", 1)[0]
    return _check("database", "Database", "ok", f"{kind} reachable; migrations applied: {', '.join(applied)}.")


def check_storage() -> dict:
    d = settings.storage_dir
    try:
        probe = d / ".write_probe"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return _check("storage", "File storage", "error", f"Cannot write to {d} ({type(exc).__name__}).",
                      "Give the TenderPilot user write access to the storage folder or set STORAGE_DIR.")
    free_gb = shutil.disk_usage(d).free / 1e9
    if free_gb < 5:
        return _check("storage", "File storage", "warning", f"Writable; only {free_gb:.1f} GB free on the drive.",
                      "Free disk space or move STORAGE_DIR: large tenders and scans need several GB.")
    return _check("storage", "File storage", "ok", f"Writable; {free_gb:.1f} GB free.")


def check_worker(db: Session) -> dict:
    queued = db.scalar(select(func.count()).select_from(ExtractionJob).where(ExtractionJob.status == "queued")) or 0
    oldest = db.scalar(select(func.min(ExtractionJob.created_at)).where(ExtractionJob.status == "queued"))
    failed = db.scalar(select(func.count()).select_from(ExtractionJob).where(
        ExtractionJob.status == "failed", ExtractionJob.updated_at >= datetime.now(timezone.utc) - timedelta(days=7))) or 0
    alive = any(t.name == "tenderpilot-worker" and t.is_alive() for t in threading.enumerate())
    if settings.run_worker_in_process and not alive:
        return _check("worker", "Processing queue", "error", f"The background worker is not running ({queued} job(s) waiting).",
                      "Restart TenderPilot (close the PowerShell window and run scripts\\start.ps1 again).")
    if oldest is not None:
        oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - oldest > timedelta(minutes=15):
            return _check("worker", "Processing queue", "warning", f"{queued} job(s) waiting, oldest for over 15 minutes.",
                          "Large scanned tenders take time; if nothing progresses, restart TenderPilot.")
    status = "warning" if failed else "ok"
    return _check("worker", "Processing queue", status, f"Worker running; {queued} queued, {failed} failed in the last 7 days.",
                  "Open the tender's Documents tab, Processing queue, to see each failure and retry." if failed else None)


def _ocr_self_test(t: ingest.TesseractAdapter, langs: list[str]) -> dict:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=300)
    page.insert_htmlbox(fitz.Rect(40, 40, 555, 260),
                        "<p style='font-size:18px'>Earnest Money Deposit Rs. 2,50,000</p>"
                        + ("<p style='font-size:18px'>बयाना राशि</p>" if "hin" in langs else ""))
    png = page.get_pixmap(dpi=200).tobytes("png")
    t0 = time.monotonic()
    res = t.ocr_png(png)
    secs = time.monotonic() - t0
    ok_en = "2,50,000" in res.text and "earnest" in res.text.lower()
    ok_hi = ("बयाना" in res.text) if "hin" in langs else None
    return {"english_ok": ok_en, "hindi_ok": ok_hi, "seconds": round(secs, 2), "confidence": res.confidence}


def check_ocr(refresh: bool) -> dict:
    def run() -> dict:
        t = ingest.TesseractAdapter()
        if not t.available():
            return _check("ocr", "OCR (scanned pages)", "error", "Tesseract was not found.",
                          "Install it: winget install UB-Mannheim.TesseractOCR, then restart TenderPilot. Or set TESSERACT_CMD.")
        ver, langs = t.version(), t.languages()
        wanted = settings.ocr_languages.split("+")
        missing = [lng for lng in wanted if lng not in langs]
        base = f"{ver or 'Tesseract'} at {t.cmd}; language data: {', '.join(langs) or 'none'}"
        base += f" (folder {settings.tessdata_dir})" if settings.tessdata_dir else ""
        if "eng" not in langs:
            return _check("ocr", "OCR (scanned pages)", "error", base + ". English data is missing.",
                          "Put eng.traineddata in the tessdata folder (backend\\data\\tessdata).")
        try:
            st = _ocr_self_test(t, langs)
        except Exception as exc:  # noqa: BLE001
            return _check("ocr", "OCR (scanned pages)", "error", base + f". Self-test failed to run ({type(exc).__name__}).",
                          "Reinstall Tesseract or check TESSERACT_CMD / TESSDATA_DIR.")
        if not st["english_ok"] or st["hindi_ok"] is False:
            return _check("ocr", "OCR (scanned pages)", "error", base + f". Self-test text was not read back correctly.",
                          "Check that the .traineddata files are complete (re-download them).", self_test=st)
        detail = base + f". Self-test read an English amount{' and Hindi text' if st['hindi_ok'] else ''} correctly in {st['seconds']}s."
        if missing:
            return _check("ocr", "OCR (scanned pages)", "warning", detail + f" Missing languages: {', '.join(missing)}.",
                          f"Download {', '.join(m + '.traineddata' for m in missing)} from github.com/tesseract-ocr/tessdata "
                          "into backend\\data\\tessdata.", self_test=st)
        return _check("ocr", "OCR (scanned pages)", "ok", detail, self_test=st)
    return _cached("ocr", refresh, run)


def check_converter(refresh: bool) -> dict:
    def run() -> dict:
        exe = convert.find_soffice()
        if not exe:
            return _check("converter", "Office conversion (.doc/.xls)", "error", "LibreOffice was not found. Legacy "
                          ".doc/.xls uploads stop at 'needs conversion'.",
                          "Install it: winget install TheDocumentFoundation.LibreOffice, then restart TenderPilot. Or set SOFFICE_CMD.")
        ver = convert.version()
        try:
            import docx
            d = docx.Document()
            d.add_paragraph("TenderPilot conversion self-test 4521")
            buf = io.BytesIO()
            d.save(buf)
            out, _, secs = convert.convert(buf.getvalue(), "docx", "doc")  # write a legacy .doc ...
            back, _, secs2 = convert.convert(out, "doc", "docx")          # ... and read it back
            ok = "4521" in "\n".join(p.text for p in docx.Document(io.BytesIO(back)).paragraphs)
        except Exception as exc:  # noqa: BLE001
            return _check("converter", "Office conversion (.doc/.xls)", "error",
                          f"{ver or 'LibreOffice'} at {exe}; self-test failed: {str(exc)[:200]}",
                          "Close any open LibreOffice windows and run the checks again; reinstall LibreOffice if it persists.")
        if not ok:
            return _check("converter", "Office conversion (.doc/.xls)", "error", f"{ver} at {exe}; round trip lost the text.",
                          "Reinstall LibreOffice.")
        return _check("converter", "Office conversion (.doc/.xls)", "ok",
                      f"{ver or 'LibreOffice'} at {exe}. Self-test: .docx to .doc and back in {secs + secs2:.1f}s; text intact.")
    return _cached("converter", refresh, run)


def check_ai(db: Session, org: Organization) -> dict:
    cfg = ai.load_config(db, org)
    row = ai.settings_row(db, org.id)
    if cfg.provider == "offline":
        return _check("ai", "AI provider", "off", "Offline: extraction uses deterministic rules only; no document text leaves this computer.",
                      "Optional: choose a provider under AI provider below if you want AI interpretations.")
    if cfg.key_error:
        return _check("ai", "AI provider", "error", cfg.key_error, "Enter the API key again under AI provider.")
    if not cfg.key_source:
        return _check("ai", "AI provider", "warning", "Anthropic selected but no API key is stored.",
                      "Paste the key into the API key field under AI provider (it is encrypted; never put it in chat or files).")
    tested = f" Last test {row.last_test_at:%d %b %Y %H:%M} UTC: {row.last_test_message}" if row.last_test_at else ""
    if row.last_test_ok is False:
        return _check("ai", "AI provider", "error", f"Connection test failed.{tested}", "Fix the problem shown, then click Test connection.")
    if row.last_test_ok is None:
        return _check("ai", "AI provider", "warning", "Key stored but not tested yet.", "Click Test connection under AI provider.")
    if not cfg.consent:
        return _check("ai", "AI provider", "warning", f"Connected but not enabled: no tender text is being sent.{tested}",
                      "Tick 'Send tender page text to the AI provider' when you are ready to use it.")
    spent = ai.month_spend(db, org.id)
    st = "warning" if spent >= cfg.budget else "ok"
    return _check("ai", "AI provider", st, f"Enabled: {ai.price(cfg.model)['label']}, effort {cfg.effort}. "
                  f"Spent USD {spent.quantize(ai.Decimal('0.01'))} of USD {cfg.budget} this month.{tested}",
                  "Monthly budget reached: raise it or wait for next month." if st == "warning" else None)


def check_frontend() -> dict:
    dist = FRONTEND_DIST / "index.html"
    if dist.exists():
        return _check("frontend", "User interface build", "ok", f"Built {datetime.fromtimestamp(dist.stat().st_mtime):%d %b %Y %H:%M}.")
    return _check("frontend", "User interface build", "error", "frontend/dist is missing.", "Run scripts\\start.ps1, which builds it.")


def check_backup() -> dict:
    folder = BACKUP_DIR
    how = ("Start menu > TenderPilot > Back up TenderPilot data" if getattr(sys, "frozen", False)
           else "stop TenderPilot and run scripts\\backup.ps1")
    zips = sorted(folder.glob("*.zip"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    if not zips or not settings.database_url.startswith("sqlite"):
        if not settings.database_url.startswith("sqlite"):
            return _check("backup", "Backups", "warning", "PostgreSQL in use: back up with pg_dump plus STORAGE_DIR.",
                          "See docs/OPERATIONS.md.")
        return _check("backup", "Backups", "warning", "No backup found in the backups folder.",
                      f"Make one now: {how}. Then copy the zip off this computer.")
    age = (time.time() - zips[-1].stat().st_mtime) / 86400
    st = "ok" if age <= 7 else "warning"
    return _check("backup", "Backups", st, f"Latest: {zips[-1].name}, {age:.0f} day(s) old.",
                  None if st == "ok" else f"Back up at least weekly: {how}.")


def run_all(db: Session, org: Organization, refresh: bool = False) -> list[dict]:
    return [check_database(db), check_storage(), check_worker(db), check_ocr(refresh), check_converter(refresh),
            check_ai(db, org), check_frontend(), check_backup()]
