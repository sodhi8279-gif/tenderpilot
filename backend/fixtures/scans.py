"""SYNTHETIC scanned and legacy-format samples for OCR / conversion verification.

scanned_pdf(): renders tender text (English and Hindi, shaped correctly) to a raster, then degrades it
like a photocopier scan: skew, speckle noise, blur and JPEG compression. The result is an image-only PDF
with no text layer, so the only way to read it is OCR.

Run `python -m fixtures.scans` from backend/ to write samples into fixtures/generated/ (git-ignored).
"""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageFilter

from .synthetic import NIT_PAGES, SYNTHETIC_BANNER, _fmt

HINDI_NOTICE = [
    "जल संसाधन विभाग, डेमो जिला (कृत्रिम उदाहरण)",
    "निविदा सूचना: 10 एमएलडी जल शोधन संयंत्र का निर्माण",
    "बयाना राशि बारह लाख पचास हजार रुपये ऑनलाइन जमा करनी होगी।",
]


def _html(lines: list[str], size: int = 13) -> str:
    esc = [ln.replace("&", "&amp;").replace("<", "&lt;") for ln in lines]
    return "".join(f"<p style='font-size:{size}px;margin:0 0 6px 0'>{ln}</p>" for ln in esc)


def _degrade(img: Image.Image, seed: int, skew: float, noise: float, jpeg_q: int) -> bytes:
    rnd = random.Random(seed)
    g = img.convert("L").rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=255)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    px = g.load()
    w, h = g.size
    for _ in range(int(w * h * noise)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice((0, 60, 200, 255))
    buf = io.BytesIO()
    g.save(buf, "JPEG", quality=jpeg_q)
    return buf.getvalue()


def scanned_pdf(pages: list[list[str]] | None = None, hindi: list[str] | None = None, dpi: int = 200,
                skew: float = 0.8, noise: float = 0.004, jpeg_q: int = 55) -> bytes:
    import fitz
    pages = pages if pages is not None else [_fmt(p) for p in NIT_PAGES[:2]]
    out = fitz.open()
    for n, lines in enumerate(pages):
        src = fitz.open()
        pg = src.new_page(width=595, height=842)
        body = _html([SYNTHETIC_BANNER]) + (_html(hindi, 15) if hindi and n == 0 else "") + _html(lines)
        pg.insert_htmlbox(fitz.Rect(48, 48, 547, 800), body)
        pix = pg.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        jpg = _degrade(img, seed=n + 7, skew=skew if n % 2 == 0 else -skew * 0.6, noise=noise, jpeg_q=jpeg_q)
        page = out.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=jpg)
    return out.tobytes()


def nit_docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_paragraph(SYNTHETIC_BANNER)
    for lines in NIT_PAGES:
        for ln in _fmt(lines):
            d.add_paragraph(ln)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def nit_xlsx() -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Key details"
    ws.append([SYNTHETIC_BANNER])
    ws.append(["Item", "Detail"])
    ws.append(["Earnest Money Deposit (EMD)", "Rs. 12,50,000 in the form of online payment or bank guarantee."])
    ws.append(["Estimated cost put to tender", "Rs. 12.50 Crore"])
    ws.append(["Period of completion", "18 months including monsoon."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def legacy_samples() -> dict[str, bytes]:
    """Real legacy .doc and .xls files, written by LibreOffice from the samples above."""
    from app.services import convert
    doc, _, _ = convert.convert(nit_docx(), "docx", "doc")
    xls, _, _ = convert.convert(nit_xlsx(), "xlsx", "xls")
    return {"SYNTHETIC_NIT_legacy.doc": doc, "SYNTHETIC_key_details_legacy.xls": xls}


if __name__ == "__main__":  # pragma: no cover
    out = Path(__file__).resolve().parent / "generated"
    out.mkdir(exist_ok=True)
    (out / "SYNTHETIC_scanned_NIT_eng_hin.pdf").write_bytes(scanned_pdf(hindi=HINDI_NOTICE))
    for name, data in legacy_samples().items():
        (out / name).write_bytes(data)
    print("Wrote samples to", out)
