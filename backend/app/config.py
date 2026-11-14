"""Runtime settings. All values come from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/ (or the bundle folder in the installed app)
# Where the database, stored files and secret key live. The installed app points this at
# %LOCALAPPDATA%\TenderPilot\data so program files and user data stay separate.
DATA_DIR = Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST") or BASE_DIR.parent / "frontend" / "dist")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or BASE_DIR.parent / "backups")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'tenderpilot.db').as_posix()}"))
    storage_dir: Path = field(default_factory=lambda: Path(_env("STORAGE_DIR", str(DATA_DIR / "storage"))))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", ""))
    token_hours: int = field(default_factory=lambda: int(_env("TOKEN_HOURS", "12")))
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "200")))
    max_zip_entries: int = field(default_factory=lambda: int(_env("MAX_ZIP_ENTRIES", "500")))
    max_zip_total_mb: int = field(default_factory=lambda: int(_env("MAX_ZIP_TOTAL_MB", "1500")))
    max_pdf_pages: int = field(default_factory=lambda: int(_env("MAX_PDF_PAGES", "5000")))
    # AI provider: "offline" (deterministic heuristics only, default) or "anthropic"
    ai_provider: str = field(default_factory=lambda: _env("AI_PROVIDER", "offline"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-opus-5-5"))
    ai_max_tokens: int = field(default_factory=lambda: int(_env("AI_MAX_TOKENS", "4000")))
    tesseract_cmd: str = field(default_factory=lambda: _env("TESSERACT_CMD", ""))
    ocr_languages: str = field(default_factory=lambda: _env("OCR_LANGUAGES", "eng+hin"))
    # Language data folder. Default: backend/data/tessdata when present (user-writable; holds eng+hin),
    # otherwise Tesseract's own tessdata folder.
    tessdata_dir: str = field(default_factory=lambda: _env("TESSDATA_DIR", ""))
    soffice_cmd: str = field(default_factory=lambda: _env("SOFFICE_CMD", ""))
    convert_timeout_s: int = field(default_factory=lambda: int(_env("CONVERT_TIMEOUT_S", "180")))
    run_worker_in_process: bool = field(default_factory=lambda: _env("RUN_WORKER", "1") == "1")
    # Re-read old documents automatically when the tender reader is upgraded. Off by default so an update never
    # changes readings of existing tenders; use "Re-read with the latest reader" on a tender to do it on purpose.
    auto_reread: bool = field(default_factory=lambda: _env("AUTO_REREAD", "0") == "1")
    low_confidence_threshold: float = 0.8
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173"))

    def __post_init__(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        if not self.tessdata_dir and (DATA_DIR / "tessdata").is_dir():
            self.tessdata_dir = str(DATA_DIR / "tessdata")
        if not self.secret_key:
            key_file = DATA_DIR / ".secret_key"
            key_file.parent.mkdir(parents=True, exist_ok=True)
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)


settings = Settings()
