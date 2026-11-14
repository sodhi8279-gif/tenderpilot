# PyInstaller build recipe for TenderPilot.exe. Run via packaging\build.ps1 (do not run directly).
# Produces dist\TenderPilot\ (folder build: starts faster and is easier to scan for antivirus than a one-file exe).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"
TESS_SRC = Path(r"C:\Program Files\Tesseract-OCR")

datas = [(str(ROOT / "frontend" / "dist"), "frontend/dist")]
# OCR engine (Apache-2.0) with only the language data TenderPilot uses: English, Hindi, script detection.
if (TESS_SRC / "tesseract.exe").exists():
    datas.append((str(TESS_SRC / "tesseract.exe"), "tesseract"))
    datas += [(str(p), "tesseract") for p in TESS_SRC.glob("*.dll")]
    datas += [(str(TESS_SRC / "LICENSE"), "tesseract")] if (TESS_SRC / "LICENSE").exists() else []
for lang in ("eng", "hin", "osd"):
    datas.append((str(BACKEND / "data" / "tessdata" / f"{lang}.traineddata"), "tesseract/tessdata"))
for pkg in ("pdfminer", "reportlab", "docx", "tzdata", "certifi", "pymupdf", "openpyxl"):
    datas += collect_data_files(pkg)

hiddenimports = (collect_submodules("app") + collect_submodules("uvicorn") + collect_submodules("fixtures")
                 + collect_submodules("anthropic") + collect_submodules("tzdata")
                 + ["seed_demo", "sqlalchemy.dialects.sqlite", "multipart", "python_multipart", "bcrypt", "jwt"])

# Python 3.15 keeps the free-threaded stable-ABI shim in a side folder PyInstaller does not scan; abi3t wheels
# (cryptography, used by pdfminer) import it at load time.
import sys
_compat = Path(sys.base_prefix) / "abi3t-compat" / "python3t.dll"
binaries = [(str(_compat), ".")] if _compat.exists() else []

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "tests", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TenderPilot",
    icon=str(ROOT / "packaging" / "tenderpilot.ico"),
    console=True,  # the window shows status and closing it stops TenderPilot
    version=str(ROOT / "packaging" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="TenderPilot")
