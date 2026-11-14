"""Clean-up of scanned vault documents: rotate, crop and deskew.

The original upload is never changed. Each edit renders one page to an image, applies the steps in a fixed
order (rotate, deskew, crop) and returns a NEW single-page PDF. Pillow only (no numpy), so it runs in the
packaged Windows app.
"""
from __future__ import annotations

import io
import statistics

from PIL import Image, ImageOps

RENDER_DPI = 200
MAX_SKEW_DEG = 6.0


class ScanFixError(ValueError):
    pass


def page_count(data: bytes, kind: str) -> int:
    if kind != "pdf":
        return 1
    import fitz
    with fitz.open(stream=data, filetype="pdf") as doc:
        return doc.page_count


def load_page(data: bytes, kind: str, page: int = 0) -> Image.Image:
    """The page as an RGB image (PDF pages rendered at 200 dpi)."""
    if kind == "pdf":
        import fitz
        with fitz.open(stream=data, filetype="pdf") as doc:
            if not 0 <= page < doc.page_count:
                raise ScanFixError(f"Page {page + 1} does not exist (the document has {doc.page_count} pages)")
            pix = doc[page].get_pixmap(dpi=RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
    elif kind == "image":
        if page != 0:
            raise ScanFixError("An image has only one page")
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # phone photos: honour the camera's orientation flag
    else:
        raise ScanFixError("Only scanned PDFs and images can be rotated, cropped or straightened")
    return img.convert("RGB")


def _row_profile_score(gray: Image.Image, angle: float) -> float:
    """Variance of row darkness after rotating: text lines give sharp peaks when the page is straight."""
    r = gray.rotate(angle, resample=Image.BILINEAR, expand=False, fillcolor=255)
    col = r.resize((1, r.height), Image.BOX)  # mean of each row
    rows = list(col.tobytes())  # one byte per row ("L" mode)
    return statistics.pvariance(rows) if len(rows) > 1 else 0.0


def detect_skew(img: Image.Image) -> float:
    """Angle in degrees (counter-clockwise positive) that straightens the text, within ±6°. 0.0 if unsure."""
    gray = img.convert("L")
    scale = 900 / max(gray.size)
    if scale < 1:
        gray = gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))), Image.BILINEAR)
    gray = gray.point(lambda p: 0 if p < 160 else 255)  # ink vs paper
    if gray.getextrema()[0] == 255:
        return 0.0  # blank page
    best, best_score = 0.0, _row_profile_score(gray, 0.0)
    base = best_score
    # coarse then fine search
    for step, span in ((0.5, MAX_SKEW_DEG), (0.1, 0.5)):
        centre = best
        a = centre - span
        while a <= centre + span + 1e-9:
            s = _row_profile_score(gray, a)
            if s > best_score:
                best, best_score = a, s
            a = round(a + step, 3)
    # Only correct when straightening clearly helps (noise on photos and blank areas otherwise).
    if base and best_score < base * 1.05:
        return 0.0
    return round(best, 1)


def apply_edits(img: Image.Image, rotate: int = 0, deskew: bool = False, crop: list[float] | None = None,
                angle: float | None = None) -> tuple[Image.Image, dict]:
    """rotate: 0/90/180/270 clockwise. crop: [x0, y0, x1, y1] as fractions (0-1) of the page after rotation.
    angle: a fixed straightening angle (overrides automatic deskew)."""
    if rotate not in (0, 90, 180, 270):
        raise ScanFixError("Rotate by 0, 90, 180 or 270 degrees")
    done: dict = {"rotate": rotate}
    if rotate:
        img = img.rotate(-rotate, expand=True)  # PIL rotates counter-clockwise
    if angle is not None:
        if abs(angle) > 45:
            raise ScanFixError("Straightening angle must be within ±45 degrees")
        skew = float(angle)
    else:
        skew = detect_skew(img) if deskew else 0.0
    if skew:
        img = img.rotate(skew, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))
    done["deskew_degrees"] = skew
    if crop:
        if len(crop) != 4:
            raise ScanFixError("Crop needs four values: left, top, right, bottom (0 to 1)")
        x0, y0, x1, y1 = (float(v) for v in crop)
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ScanFixError("Crop values must be fractions with left < right and top < bottom")
        if (x1 - x0) < 0.05 or (y1 - y0) < 0.05:
            raise ScanFixError("The crop area is too small")
        w, h = img.size
        img = img.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
        done["crop"] = [x0, y0, x1, y1]
    done["size_px"] = list(img.size)
    return img, done


def to_pdf(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=RENDER_DPI)
    return buf.getvalue()


def to_png(img: Image.Image, max_side: int = 1400) -> bytes:
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
