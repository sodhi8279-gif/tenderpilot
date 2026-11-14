"""Tender forms supplied as PDF.

* Fillable PDF (AcroForm): the form's own fields are listed as placeholders, mapped by a reviewer exactly like DOCX
  placeholders, and filled in a copy of the PDF.
* Flat or scanned PDF: a reviewer places text boxes on the page (page number, position as fractions of the page,
  font size, width). Values are printed in a copy at those positions.

The uploaded form is never modified. Missing values are printed as «MISSING: name» and recorded, like DOCX forms.
Nothing is signed or stamped here.
"""
from __future__ import annotations

import fitz  # PyMuPDF

MISSING = "«MISSING: {}»"
TEXT_TYPES = {fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX}
CHECK_TYPES = {fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON}
TRUE_WORDS = {"yes", "y", "true", "1", "x", "✓", "on", "checked"}


class PdfFormError(ValueError):
    pass


def _open(data: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfFormError("The file could not be opened as a PDF") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfFormError("The PDF is password-protected. Remove the password or upload an unprotected copy.")
    return doc


def inspect_form(data: bytes) -> dict:
    """Fields of a fillable PDF (empty list for flat/scanned forms) and page sizes."""
    with _open(data) as doc:
        fields = []
        seen = set()
        for pno, page in enumerate(doc):
            for w in page.widgets() or []:
                name = (w.field_name or "").strip()
                if not name or name in seen or w.field_type not in TEXT_TYPES | CHECK_TYPES:
                    continue
                seen.add(name)
                fields.append({"name": name, "page": pno + 1, "type": "check" if w.field_type in CHECK_TYPES else "text",
                               "label": (w.field_label or "").strip() or None})
        pages = [{"width": p.rect.width, "height": p.rect.height} for p in doc]
    return {"fields": fields, "pages": pages}


def page_png(data: bytes, page_index: int, dpi: int = 110) -> bytes:
    with _open(data) as doc:
        if not 0 <= page_index < doc.page_count:
            raise PdfFormError(f"Page {page_index + 1} does not exist")
        return doc[page_index].get_pixmap(dpi=dpi).tobytes("png")


def fill_acroform(data: bytes, values: dict[str, str | None], missing: list[str]) -> bytes:
    """Fill a copy of a fillable PDF. Fields keep their place and appearance; the form stays editable."""
    with _open(data) as doc:
        for page in doc:
            for w in page.widgets() or []:
                name = (w.field_name or "").strip()
                if name not in values:
                    continue
                v = values[name]
                if w.field_type in CHECK_TYPES:
                    if v is None or str(v).strip() == "":
                        missing.append(name)
                        continue
                    w.field_value = w.on_state() if str(v).strip().lower() in TRUE_WORDS else "Off"
                else:
                    if v is None or str(v).strip() == "":
                        missing.append(name)
                        v = MISSING.format(name)
                    w.field_value = str(v)
                w.update()
        return doc.tobytes(garbage=3, deflate=True)


def validate_layout(boxes: list[dict], page_count: int) -> list[dict]:
    """Overlay boxes: name, page (1-based), x, y (top-left, fractions 0-1), width (fraction), font_size (pt)."""
    out, names = [], set()
    for b in boxes:
        name = str(b.get("name") or "").strip()
        if not name or len(name) > 120:
            raise PdfFormError("Every box needs a name (up to 120 characters)")
        if name in names:
            raise PdfFormError(f"Box name '{name}' is used twice")
        names.add(name)
        try:
            page, x, y = int(b.get("page", 1)), float(b["x"]), float(b["y"])
            width, size = float(b.get("width", 0.4)), float(b.get("font_size", 10))
        except (KeyError, TypeError, ValueError) as exc:
            raise PdfFormError(f"Box '{name}': page, x and y must be numbers") from exc
        if not 1 <= page <= page_count:
            raise PdfFormError(f"Box '{name}': page {page} does not exist (the form has {page_count} pages)")
        if not (0 <= x < 1 and 0 <= y < 1 and 0.02 <= width <= 1 and x + width <= 1.0001):
            raise PdfFormError(f"Box '{name}' must sit inside the page")
        if not 5 <= size <= 24:
            raise PdfFormError(f"Box '{name}': font size must be 5 to 24 pt")
        out.append({"name": name, "page": page, "x": round(x, 4), "y": round(y, 4), "width": round(width, 4), "font_size": size})
    return out


def _fit_size(text: str, box: fitz.Rect, size: float) -> float:
    """Largest font size (down to 5 pt) at which the text fits the box, measured on a throwaway page."""
    with fitz.open() as scratch:
        pg = scratch.new_page(width=box.width + 10, height=box.height + 10)
        trial = fitz.Rect(0, 0, box.width, box.height)
        while size > 5 and pg.insert_textbox(trial, text, fontsize=size, fontname="helv") < 0:
            size -= 0.5
    return max(size, 5.0)


def fill_overlay(data: bytes, boxes: list[dict], values: dict[str, str | None], missing: list[str]) -> bytes:
    """Print values on a copy of a flat or scanned PDF at the reviewer-placed positions."""
    with _open(data) as doc:
        for b in boxes:
            page = doc[b["page"] - 1]
            r = page.rect
            v = values.get(b["name"])
            if v is None or str(v).strip() == "":
                missing.append(b["name"])
                v, color = MISSING.format(b["name"]), (0.75, 0.1, 0.1)
            else:
                color = (0, 0, 0.35)  # dark blue, as if typed into the blank
            x0, y0 = r.x0 + b["x"] * r.width, r.y0 + b["y"] * r.height
            box = fitz.Rect(x0, y0, x0 + b["width"] * r.width, min(r.y1, y0 + b["font_size"] * 4.5))
            text = str(v)
            size = _fit_size(text, box, b["font_size"])
            page.insert_textbox(box, text, fontsize=size, fontname="helv", color=color, overlay=True)
        return doc.tobytes(garbage=3, deflate=True)
