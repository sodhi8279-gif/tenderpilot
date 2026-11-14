"""LibreOffice adapter: legacy .doc/.xls to .docx/.xlsx for reading.

The original bytes are passed in and never modified; callers store the output as a separate
derived file. Conversion runs headless with a dedicated profile, no macros, a timeout, and one
conversion at a time (LibreOffice cannot share a profile between concurrent processes).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..config import DATA_DIR, settings

_LOCK = threading.Lock()
_CANDIDATES = (r"C:\Program Files\LibreOffice\program\soffice.exe",
               r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
               "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice",
               "/Applications/LibreOffice.app/Contents/MacOS/soffice")
TARGETS = {"doc": "docx", "xls": "xlsx", "ppt": "pdf", "rtf": "docx", "odt": "docx", "ods": "xlsx"}


class ConversionError(Exception):
    pass


def find_soffice() -> str | None:
    for c in (settings.soffice_cmd, shutil.which("soffice"), *_CANDIDATES):
        if c and Path(c).exists():
            return c
    return None


def available() -> bool:
    return find_soffice() is not None


def version() -> str | None:
    exe = find_soffice()
    if not exe:
        return None
    # On Windows soffice.exe detaches from the console; soffice.com prints the version.
    com = Path(exe).with_suffix(".com")
    cmd = str(com) if com.exists() else exe
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=60)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def legacy_type(filename: str) -> str | None:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return ext if ext in TARGETS else None


def convert(data: bytes, src_ext: str, target: str | None = None) -> tuple[bytes, str, float]:
    """Return (converted_bytes, target_ext, seconds). Raises ConversionError with an actionable message."""
    exe = find_soffice()
    if not exe:
        raise ConversionError("LibreOffice is not installed. Install it (winget install TheDocumentFoundation.LibreOffice) "
                              "or set SOFFICE_CMD, then retry the document.")
    target = target or TARGETS.get(src_ext, "pdf")
    profile = (DATA_DIR / "lo_profile").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    with _LOCK, tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"input.{src_ext}"
        src.write_bytes(data)
        outdir = Path(td) / "out"
        outdir.mkdir()
        cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nologo",
               "--nodefault", "--nolockcheck", "--convert-to", target, "--outdir", str(outdir), str(src)]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.convert_timeout_s)
        except subprocess.TimeoutExpired:
            raise ConversionError(f"LibreOffice did not finish within {settings.convert_timeout_s}s. The file may be "
                                  "very large or damaged; raise CONVERT_TIMEOUT_S or convert it manually.") from None
        except OSError as exc:
            raise ConversionError(f"LibreOffice could not be started ({type(exc).__name__}). Check SOFFICE_CMD.") from None
        out = outdir / f"input.{target}"
        if not out.exists() or out.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            raise ConversionError("LibreOffice produced no output. The file may be damaged, password-protected or not "
                                  f"really a .{src_ext} file." + (f" ({detail})" if detail else ""))
        return out.read_bytes(), target, round(time.monotonic() - t0, 2)
