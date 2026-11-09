"""Object storage behind an S3-compatible-style interface. Local filesystem for development.

Keys are opaque, generated server-side ("org/<id>/<uuid>.<ext>") so user filenames never
become filesystem paths. Stored originals are written once and never modified.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Protocol

from .config import settings


class Storage(Protocol):
    def put(self, org_id: int, data: bytes, ext: str) -> str: ...
    def get(self, key: str) -> bytes: ...
    def path(self, key: str) -> Path: ...
    def exists(self, key: str) -> bool: ...


SAFE_EXT = {"pdf", "docx", "xlsx", "xlsm", "xls", "csv", "png", "jpg", "jpeg", "tif", "tiff", "zip", "txt", "json", "bin"}


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("Invalid storage key")
        return p

    def put(self, org_id: int, data: bytes, ext: str) -> str:
        ext = ext.lower().lstrip(".")
        if ext not in SAFE_EXT:
            ext = "bin"
        key = f"org{int(org_id)}/{uuid.uuid4().hex}.{ext}"
        p = self._resolve(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "xb") as fh:  # exclusive create: never overwrite
            fh.write(data)
        return key

    def get(self, key: str) -> bytes:
        return self._resolve(key).read_bytes()

    def path(self, key: str) -> Path:
        return self._resolve(key)

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


storage: Storage = LocalStorage(settings.storage_dir)
