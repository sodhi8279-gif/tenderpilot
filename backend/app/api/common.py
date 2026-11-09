from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException, UploadFile
from sqlalchemy import inspect

from ..config import settings


def to_dict(obj, exclude: set[str] | None = None) -> dict:
    exclude = exclude or set()
    out = {}
    for col in inspect(obj).mapper.column_attrs:
        k = col.key
        if k in exclude:
            continue
        v = getattr(obj, k)
        if isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, (datetime, date)):
            v = v.isoformat()
        out[k] = v
    return out


def mask(value: str | None) -> str | None:
    if not value:
        return value
    return value[:2] + "•" * max(len(value) - 4, 0) + value[-2:] if len(value) > 4 else "••••"


async def read_upload(file: UploadFile) -> bytes:
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")
    if not data:
        raise HTTPException(400, "Empty file")
    return data


def parse_date_field(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, f"Invalid date '{v}' (use YYYY-MM-DD)")
