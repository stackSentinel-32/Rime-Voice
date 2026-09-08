"""Shared fixtures for the hermetic acceptance/unit tests (no external services)."""
from __future__ import annotations

import pytest

from agent.event_log import EventLogger
from agent.tool_client import LocalToolClient
from agent.turn_manager import TurnManager
from backend.db import Base, make_engine, make_session_factory
from backend.seed import seed
from redis_state.session_store import SessionStore, make_redis_client


class FakeTTS:
    """Stand-in for the Rime stream: records speak/cancel so the test can measure the
    cancel path and assert nothing stale was spoken."""

    def __init__(self):
        self.active = False
        self.current_text = None
        self.current_version = None
        self.cancel_count = 0
        self.spoken: list[tuple[str, int]] = []

    def speak(self, text: str, turn_version: int) -> None:
        self.active = True
        self.current_text = text
        self.current_version = turn_version
        self.spoken.append((text, turn_version))

    def cancel(self) -> None:
        self.active = False
        self.cancel_count += 1


@pytest.fixture
def db_sf():
    engine = make_engine("sqlite://")  # shared in-memory DB (StaticPool)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    seed(sf, n=40)
    return sf


@pytest.fixture
def make_tm(db_sf):
    def _factory(session_id: str = "test-sess"):
        store = SessionStore(make_redis_client(use_fake=True), session_id)
        store.reset()
        events = EventLogger(session_id)
        tts = FakeTTS()
        tools = LocalToolClient(db_sf)
        tm = TurnManager(session_id, store, events, tts, tools,
                         render=lambda n, p: f"{n}: booking {p.get('id')} on {p.get('date')} ({p.get('status')})")
        return tm, store, events, tts

    return _factory
