"""Runtime settings. All values come from environment variables (see .env.example)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'data' / 'tenderpilot.db').as_posix()}"))
    storage_dir: Path = field(default_factory=lambda: Path(_env("STORAGE_DIR", str(BASE_DIR / "data" / "storage"))))
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
    run_worker_in_process: bool = field(default_factory=lambda: _env("RUN_WORKER", "1") == "1")
    low_confidence_threshold: float = 0.8
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "http://localhost:5173"))

    def __post_init__(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite:///"):
            Path(self.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        if not self.secret_key:
            key_file = BASE_DIR / "data" / ".secret_key"
            key_file.parent.mkdir(parents=True, exist_ok=True)
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)


settings = Settings()
