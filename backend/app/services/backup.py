"""Full data backup: database (consistent online copy) + stored files + secret key, as one zip."""
from __future__ import annotations

import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import BACKUP_DIR, DATA_DIR, settings


class BackupError(RuntimeError):
    pass


def make_backup(label: str = "") -> Path:
    """Write tenderpilot-<date>[-label].zip into the backups folder and return its path.
    Safe while TenderPilot is running (SQLite online backup). Raises BackupError if nothing can be backed up."""
    if not settings.database_url.startswith("sqlite:///"):
        raise BackupError("PostgreSQL in use: back it up with pg_dump together with the storage folder.")
    db_path = Path(settings.database_url.replace("sqlite:///", ""))
    if not db_path.exists():
        raise BackupError("Nothing to back up yet: TenderPilot has not been started on this computer.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"tenderpilot-{datetime.now():%Y%m%d-%H%M%S}{('-' + label) if label else ''}.zip"
    tmp = BACKUP_DIR / f".backup-db-{datetime.now():%H%M%S%f}.tmp"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(tmp)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "tenderpilot.db")
            for f in DATA_DIR.rglob("*"):
                rel = f.relative_to(DATA_DIR)
                if f.is_file() and (rel.parts[0] == "storage" or rel.as_posix() == ".secret_key"):
                    z.write(f, rel.as_posix())
        with zipfile.ZipFile(out) as z:  # read it back: a backup that cannot be opened is no backup
            if z.testzip() is not None:
                raise BackupError("The backup file failed its integrity check")
    finally:
        tmp.unlink(missing_ok=True)
    return out
