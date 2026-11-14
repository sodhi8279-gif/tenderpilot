"""Minimal ordered migration runner (schema_version table).

0001 creates the baseline schema from the models. Add new migrations as functions appended to
MIGRATIONS; each runs once, in order, inside a transaction. (Alembic can replace this later.)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import inspect, text

from . import models  # noqa: F401  (register tables)
from .config import DATA_DIR, settings
from .db import Base, engine

log = logging.getLogger("tenderpilot.migrations")

# RULE for every migration added after 0003: additive only. Create new tables or add new nullable
# columns; never drop, rename or rewrite existing tables, columns or rows. Existing data must read back
# exactly as before (tests/test_data_safety.py checks this on a pre-update database).


def _m0001_baseline(conn) -> None:
    Base.metadata.create_all(bind=conn)


def _m0002_integrations(conn) -> None:
    """AI settings/usage tables; derived-conversion columns on source_documents (existing data kept)."""
    Base.metadata.create_all(bind=conn)  # creates only missing tables
    cols = {c["name"] for c in inspect(conn).get_columns("source_documents")}
    for name, ddl in (("converted_storage_key", "VARCHAR(300)"), ("converted_kind", "VARCHAR(20)"),
                      ("converted_by", "VARCHAR(80)")):
        if name not in cols:
            conn.execute(text(f"ALTER TABLE source_documents ADD COLUMN {name} {ddl}"))


def _m0003_similarity_links(conn) -> None:
    """Per-tender similarity decisions on evidence links (existing links are checklist links: purpose 'task')."""
    cols = {c["name"] for c in inspect(conn).get_columns("evidence_links")}
    for name, ddl in (("purpose", "VARCHAR(20) DEFAULT 'task'"), ("criterion_key", "VARCHAR(120)")):
        if name not in cols:
            conn.execute(text(f"ALTER TABLE evidence_links ADD COLUMN {name} {ddl}"))


MIGRATIONS = [("0001_baseline", _m0001_baseline), ("0002_integrations", _m0002_integrations),
              ("0003_similarity_links", _m0003_similarity_links)]


def pending_migrations() -> list[str]:
    with engine.connect() as conn:
        if not inspect(conn).has_table("schema_version"):
            return [m for m, _ in MIGRATIONS]
        done = {r[0] for r in conn.execute(text("SELECT id FROM schema_version"))}
    return [m for m, _ in MIGRATIONS if m not in done]


def backup_before_update(pending: list[str]) -> Path | None:
    """Copy the SQLite database before any migration touches it. Returns the copy's path.

    Skipped for a brand-new (empty) database and for non-SQLite databases (back those up with their own tools)."""
    if not pending or not settings.database_url.startswith("sqlite:///"):
        return None
    db_path = Path(settings.database_url.replace("sqlite:///", ""))
    if not db_path.exists():
        return None
    with engine.connect() as conn:
        if not inspect(conn).get_table_names():
            return None
    out_dir = DATA_DIR / "backups-before-update"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"tenderpilot-before-{pending[0]}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)  # consistent copy, includes anything still in the WAL file
        finally:
            dst.close()
    finally:
        src.close()
    log.info("Database copied to %s before applying %s", dest, ", ".join(pending))
    return dest


def migrate() -> list[str]:
    pending = pending_migrations()
    if pending:
        backup_before_update(pending)  # raises (and so blocks the update) if the copy cannot be made
    applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (id VARCHAR(100) PRIMARY KEY)"))
        done = {r[0] for r in conn.execute(text("SELECT id FROM schema_version"))}
        for mid, fn in MIGRATIONS:
            if mid in done:
                continue
            fn(conn)
            conn.execute(text("INSERT INTO schema_version (id) VALUES (:i)"), {"i": mid})
            applied.append(mid)
    return applied
