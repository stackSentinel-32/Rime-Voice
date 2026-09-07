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


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
