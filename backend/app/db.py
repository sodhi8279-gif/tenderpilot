from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

# autoflush=True: queries must see status changes made earlier in the same unit of work
# (e.g. rules superseded by an amendment before new rules are proposed).
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class DecimalStr(TypeDecorator):
    """Exact decimal stored as text: no float rounding on SQLite or Postgres."""

    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        try:
            return str(Decimal(str(value)))
        except InvalidOperation as exc:
            raise ValueError(f"Not a decimal: {value!r}") from exc

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
