"""Tender file ingestion: real-format detection, bounded ZIP expansion, page-level text/OCR.

Every page of every document ends in exactly one state: processed, failed, unreadable,
duplicate or excluded (with a reason). Nothing is truncated silently.
"""
from __future__ import annotations

import csv
import hashlib
import io
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..config import settings

MIN_TEXT_CHARS = 25


class UnsafeFile(Exception):
    pass


def sniff_kind(data: bytes, filename: str) -> str:
    """Identify the actual format from content (not the extension)."""
    head = data[:8]
    name = filename.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "image"
    if head[:3] == b"\xff\xd8\xff":
        return "image"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # OLE2: legacy .xls/.doc
        return "xls" if name.endswith(".xls") else "ole_legacy"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            raise UnsafeFile("Corrupt ZIP container")
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsm" if "xl/vbaProject.bin" in names else "xlsx"
        return "zip"
    if name.endswith(".csv"):
        try:
            data[:4096].decode("utf-8")
            return "csv"
        except UnicodeDecodeError:
            pass
    raise UnsafeFile("Unsupported or unrecognised file format")


def safe_zip_members(data: bytes) -> list[tuple[str, bytes]]:
    """Expand a ZIP with bounded size/count and path-traversal rejection."""
    out: list[tuple[str, bytes]] = []
    total = 0
    limit_total = settings.max_zip_total_mb * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > settings.max_zip_entries:
            raise UnsafeFile(f"ZIP has {len(infos)} entries; limit is {settings.max_zip_entries}")
        for info in infos:
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
                raise UnsafeFile(f"Unsafe path in ZIP: {info.filename}")
            if info.flag_bits & 0x1:
                raise UnsafeFile(f"Encrypted ZIP entry: {info.filename} (supply an unencrypted package)")
            if info.file_size > settings.max_upload_mb * 1024 * 1024:
                raise UnsafeFile(f"ZIP entry too large: {info.filename}")
            total += info.file_size
            if total > limit_total:
                raise UnsafeFile("ZIP expands beyond the configured limit (possible zip bomb)")
            with z.open(info) as fh:
                chunk = fh.read(info.file_size + 1)
            if len(chunk) != info.file_size:
                raise UnsafeFile(f"ZIP entry size mismatch: {info.filename}")
            if p.name.startswith(".") or p.parts[0] == "__MACOSX":
                continue
            out.append((p.name, chunk))
    return out


# ------------------------------------------------------------------- OCR adapter
@dataclass
class OcrResult:
    text: str
    confidence: float


class OcrAdapter:
    name = "none"

    def available(self) -> bool:
        return False

    def ocr_png(self, png: bytes) -> OcrResult:  # pragma: no cover
        raise RuntimeError("No OCR engine installed")


class TesseractAdapter(OcrAdapter):
    name = "tesseract"

    def __init__(self) -> None:
        self.cmd = settings.tesseract_cmd or shutil.which("tesseract") or ""
        if not self.cmd:
            default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
            if default.exists():
                self.cmd = str(default)

    def available(self) -> bool:
        return bool(self.cmd)

    def _langs(self) -> str:
        try:
            out = subprocess.run([self.cmd, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
            have = {ln.strip() for ln in out.splitlines()[1:]}
            wanted = [lang for lang in settings.ocr_languages.split("+") if lang in have]
            return "+".join(wanted) or "eng"
        except (OSError, subprocess.SubprocessError):
            return "eng"

    def ocr_png(self, png: bytes) -> OcrResult:
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "page.png"
            img.write_bytes(png)
            proc = subprocess.run([self.cmd, str(img), "stdout", "-l", self._langs(), "tsv"],
                                  capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise RuntimeError("Tesseract failed")
        words, confs = [], []
        rows = list(csv.reader(io.StringIO(proc.stdout), delimiter="\t"))
        line_key = None
        for r in rows[1:]:
            if len(r) < 12 or not r[11].strip():
                continue
            key = (r[2], r[3], r[4])
            if line_key is not None and key != line_key:
                words.append("\n")
            line_key = key
            words.append(r[11])
            try:
                c = float(r[10])
                if c >= 0:
                    confs.append(c)
            except ValueError:
                pass
        text = " ".join(words).replace(" \n ", "\n")
        conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return OcrResult(text=text, confidence=round(conf, 3))


def get_ocr() -> OcrAdapter:
    t = TesseractAdapter()
    return t if t.available() else OcrAdapter()


# ------------------------------------------------------------------- page extraction
@dataclass
class PageOut:
    index: int
    label: str | None
    status: str
    reason: str | None
    text: str | None
    text_source: str | None
    confidence: float | None
    width: float | None = None
    height: float | None = None


def text_hash(text: str) -> str:
    norm = " ".join(text.split()).lower()
    return hashlib.sha256(norm.encode()).hexdigest()


def open_pdf(data: bytes, password: str | None = None):
    import fitz  # PyMuPDF
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PermissionError("PDF is password protected")
    return doc


def pdf_page_count(data: bytes, password: str | None = None) -> int:
    doc = open_pdf(data, password)
    try:
        return doc.page_count
    finally:
        doc.close()


def process_pdf_page(doc, index: int, ocr: OcrAdapter) -> PageOut:
    page = doc.load_page(index)
    label = page.get_label() or None
    w, h = page.rect.width, page.rect.height
    try:
        text = page.get_text("text") or ""
    except Exception as exc:  # noqa: BLE001 - corrupt page objects
        return PageOut(index, label, "failed", f"Text extraction error: {type(exc).__name__}", None, None, None, w, h)
    if len(text.strip()) >= MIN_TEXT_CHARS:
        return PageOut(index, label, "processed", None, text, "text_layer", 0.99, w, h)
    # No usable text layer: scanned page
    if not ocr.available():
        return PageOut(index, label, "unreadable",
                       "No text layer and no OCR engine installed (install Tesseract to read scanned pages)",
                       text or None, None, None, w, h)
    try:
        pix = page.get_pixmap(dpi=300)
        res = ocr.ocr_png(pix.tobytes("png"))
    except Exception as exc:  # noqa: BLE001
        return PageOut(index, label, "failed", f"OCR failed: {type(exc).__name__}", None, None, None, w, h)
    if len(res.text.strip()) < MIN_TEXT_CHARS:
        return PageOut(index, label, "unreadable", "OCR produced no legible text", res.text, "ocr", res.confidence, w, h)
    return PageOut(index, label, "processed", None, res.text, "ocr", res.confidence, w, h)


def process_image(data: bytes, ocr: OcrAdapter) -> PageOut:
    if not ocr.available():
        return PageOut(0, None, "unreadable", "Image requires OCR; no OCR engine installed", None, None, None)
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    res = ocr.ocr_png(buf.getvalue())
    status = "processed" if len(res.text.strip()) >= MIN_TEXT_CHARS else "unreadable"
    return PageOut(0, None, status, None if status == "processed" else "OCR produced no legible text",
                   res.text, "ocr", res.confidence, img.width, img.height)


def docx_text_pages(data: bytes) -> list[PageOut]:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    text = "\n".join(parts)
    status = "processed" if text.strip() else "unreadable"
    return [PageOut(0, "DOCX", status, None if text.strip() else "Empty document", text, "docx", 0.99)]


def xlsx_text_pages(data: bytes) -> list[PageOut]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=True)
    out = []
    for i, ws in enumerate(wb.worksheets):
        rows = []
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if any(vals):
                rows.append(" | ".join(vals))
        text = "\n".join(rows)
        hidden = ws.sheet_state != "visible"
        out.append(PageOut(i, f"Sheet: {ws.title}" + (" (hidden)" if hidden else ""),
                           "processed" if text.strip() else "excluded",
                           None if text.strip() else "Empty sheet", text, "xlsx", 0.99))
    wb.close()
    return out


def csv_text_pages(data: bytes) -> list[PageOut]:
    text = data.decode("utf-8", errors="replace")
    return [PageOut(0, "CSV", "processed" if text.strip() else "excluded", None, text, "csv", 0.99)]


def guess_doc_class(filename: str, first_text: str) -> str:
    s = (filename + " " + first_text[:1500]).lower()
    rules = [
        ("corrigendum", ["corrigendum", "corrigenda"]), ("addendum", ["addendum", "addenda"]),
        ("prebid_reply", ["pre-bid", "prebid", "pre bid"]), ("boq", ["bill of quantities", "boq", "schedule of rates", "price schedule"]),
        ("nit", ["notice inviting tender", "nit ", "e-tender notice", "invitation for bids"]),
        ("gcc", ["general conditions of contract", "gcc"]), ("scc", ["special conditions of contract", "scc", "particular conditions"]),
        ("itb", ["instructions to bidders", "itb"]), ("bds", ["bid data sheet", "tender data sheet"]),
        ("qualification", ["qualification criteria", "eligibility criteria"]), ("specification", ["technical specification"]),
        ("forms", ["bidding forms", "form of bid", "letter of bid"]), ("drawing", ["drawing", "layout plan"]),
    ]
    for cls, keys in rules:
        if any(k in s for k in keys):
            return cls
    return "unclassified"
