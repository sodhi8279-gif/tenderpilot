"""Minimal ordered migration runner (schema_version table).

0001 creates the baseline schema from the models. Add new migrations as functions appended to
MIGRATIONS; each runs once, in order, inside a transaction. (Alembic can replace this later.)
"""
from __future__ import annotations

from sqlalchemy import text

from . import models  # noqa: F401  (register tables)
from .db import Base, engine


def _m0001_baseline(conn) -> None:
    Base.metadata.create_all(bind=conn)


MIGRATIONS = [("0001_baseline", _m0001_baseline)]


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
