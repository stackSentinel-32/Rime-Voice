"""Version monotonicity under rapid double interruption (roadmap §4 / Risk: off-by-one).

Writing this early, not at the end — off-by-one version bugs are the most likely
failure mode of the whole scheme.
"""
from __future__ import annotations

import asyncio

from redis_state.session_store import SessionStore, make_redis_client


def test_increment_is_monotonic():
    store = SessionStore(make_redis_client(use_fake=True), "mono")
    store.reset()
    store.create_session()
    versions = [store.increment_turn_version() for _ in range(50)]
    assert versions == list(range(2, 52))
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_concurrent_increments_no_dupes():
    store = SessionStore(make_redis_client(use_fake=True), "mono-conc")
    store.reset()
    store.create_session()

    async def bump():
        return store.increment_turn_version()

    async def run():
        return await asyncio.gather(*[bump() for _ in range(30)])

    results = asyncio.run(run())
    assert len(set(results)) == len(results), "duplicate versions issued"


def test_rapid_double_interruption(make_tm):
    """Two interruptions in quick succession bump v1->v2->v3; only v3 work applies."""
    tm, store, events, tts = make_tm("rapid")

    async def scenario():
        await tm.dispatch_tool("lookup_booking", {"booking_id": 12, "delay_ms": 400})
        tts.speak("checking...", store.get_turn_version())
        await asyncio.sleep(0.05)
        tm.on_interrupt()  # -> v2
        await asyncio.sleep(0.02)
        tm.on_interrupt()  # -> v3
        await tm.dispatch_tool("update_booking", {"booking_id": 12, "changes": {"date": "2026-09-14"}})
        await tm.join()

    asyncio.run(scenario())
    assert store.get_turn_version() == 3
    results = [e for e in events.to_dict()["events"] if e["type"] == "tool_call_result"]
    # the v1 lookup is stale; the v3 update applies
    stale = [e for e in results if e["action"] == "discarded_stale"]
    applied = [e for e in results if e["action"] == "applied"]
    assert len(stale) == 1 and stale[0]["turn_version_at_result"] == 1
    assert len(applied) == 1 and applied[0]["turn_version_at_result"] == 3
