"""SQLAlchemy 2.x engine / session plumbing.

The schema is written to be portable: MySQL 8 (the deployment target) and
SQLite (used by the test-suite so tests never touch a real database).
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def utcnow() -> dt.datetime:
    """Timezone-naive UTC timestamp (MySQL DATETIME has no tz)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


#: Deterministic constraint names. Without these, Alembic cannot reliably
#: drop or alter a constraint it did not create, and MySQL/SQLite invent
#: different names for the same thing.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _build_engine():
    url = settings.sqlalchemy_url
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        eng = create_engine(
            url,
            echo=settings.db_echo,
            future=True,
            connect_args=connect_args,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng

    return create_engine(
        url,
        echo=settings.db_echo,
        future=True,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
    )


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
)


def get_db() -> Generator:
    """FastAPI dependency - one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator:
    """Context-managed session for scripts and background tasks."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
