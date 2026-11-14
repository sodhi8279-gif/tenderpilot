"""Minimal ordered migration runner (schema_version table).

0001 creates the baseline schema from the models. Add new migrations as functions appended to
MIGRATIONS; each runs once, in order, inside a transaction. (Alembic can replace this later.)
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from . import models  # noqa: F401  (register tables)
from .db import Base, engine


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


def migrate() -> list[str]:
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
