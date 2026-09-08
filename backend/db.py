"""Database engine/session helpers.

Backend-agnostic: `DATABASE_URL` selects Postgres (full app) or SQLite (hermetic
tests). In-memory SQLite uses a StaticPool so every session shares one DB.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()


def make_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "sqlite:///./bookings.db")
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if url in ("sqlite://", "sqlite:///:memory:") or ":memory:" in url:
            # single shared in-memory DB across all sessions/threads
            return create_engine(url, connect_args=connect_args, poolclass=StaticPool, future=True)
        return create_engine(url, connect_args=connect_args, future=True)
    return create_engine(url, pool_pre_ping=True, future=True)


def ensure_schema(engine) -> None:
    """Lightweight auto-migration: add columns introduced after the table existed.

    create_all() only creates missing TABLES, never missing COLUMNS — existing
    Postgres instances need an idempotent ALTER for the `phone` column.
    """
    from sqlalchemy import text

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS phone VARCHAR"))
        except Exception:
            pass  # sqlite or unsupported variant; create_all() covered fresh DBs


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
