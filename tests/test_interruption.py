"""Automated acceptance test — proves the §1 claim via the three §7 invariants.

Scenario (roadmap §5.2 mid-tool-call interruption):
  t=0     dispatch slow lookup_booking(12) tagged v1 (TOOL_CALL_DELAY_MS delay), agent speaks.
  t=1500  user interrupts -> v1->v2, TTS cancelled, fresh update_booking(date=Friday) tagged v2.
  t~4000  stale lookup (v1) arrives -> discarded_stale (v1 != v2).
  t~1500  update (v2) arrives -> applied + committed.

Invariants asserted:
  1. cancellation latency  < CANCEL_LATENCY_THRESHOLD_MS
  2. no stale application   (no result with tag != current has action "applied")
  3. final-state correctness (DB reflects only the corrected request)

Runs are parameterized cold (fresh state) and warm (repeated) — reported separately.
Timings are scaled down by default so the suite is fast; override with env vars to
reproduce the exact 4000/1500 numbers from the roadmap.
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from backend.tools import booking_to_dict
from backend.models import Booking

THRESHOLD_MS = int(os.getenv("CANCEL_LATENCY_THRESHOLD_MS", "300"))
DELAY_MS = int(os.getenv("TOOL_CALL_DELAY_MS", "800"))
INTERRUPT_AT_MS = int(os.getenv("INTERRUPT_AT_MS", "300"))
FRIDAY = "2026-09-18"
BOOKING_ID = 12


async def _run_interruption_scenario(tm, store, events, tts, db_sf):
    # v1: slow lookup in flight; agent starts speaking
    await tm.dispatch_tool("lookup_booking", {"booking_id": BOOKING_ID, "delay_ms": DELAY_MS})
    tts.speak("Let me check booking 12 for Thursday...", store.get_turn_version())

    # user interrupts partway through the in-flight call
    await asyncio.sleep(INTERRUPT_AT_MS / 1000.0)
    tm.on_interrupt(reason="interrupt")

    # v2: corrected request dispatched immediately (fast)
    await tm.dispatch_tool("update_booking", {"booking_id": BOOKING_ID, "changes": {"date": FRIDAY}})

    # let everything (including the delayed stale result) settle
    await tm.join()
    return events.to_dict()


@pytest.mark.parametrize("run_kind", ["cold", "warm"])
def test_acceptance_interruption(make_tm, db_sf, run_kind):
    tm, store, events, tts = make_tm(f"acc-{run_kind}")
    if run_kind == "warm":
        # prime once, then run again on the same logic path
        asyncio.run(_run_interruption_scenario(*make_tm("acc-warm-prime"), db_sf))
    log = asyncio.run(_run_interruption_scenario(tm, store, events, tts, db_sf))
    evs = log["events"]

    # --- Invariant 1: cancellation latency ---
    cancels = [e for e in evs if e["type"] == "tts_cancel"]
    assert cancels, "no tts_cancel event logged"
    latency = cancels[0]["latency_ms"]
    assert latency < THRESHOLD_MS, f"cancel latency {latency}ms >= {THRESHOLD_MS}ms"

    # --- Invariant 2: no stale application ---
    for e in evs:
        if e["type"] == "tool_call_result":
            if e["turn_version_at_result"] != e["current_turn_version"]:
                assert e["action"] == "discarded_stale", f"stale result was {e['action']}"

    # exactly one discarded_stale (the v1 lookup) and one applied (the v2 update)
    results = [e for e in evs if e["type"] == "tool_call_result"]
    actions = sorted(e["action"] for e in results)
    assert actions == ["applied", "discarded_stale"], actions

    # the stale result returns before TTS.speak(), so the agent's final utterance
    # reflects only the latest turn — never a stale result
    assert tts.spoken, "agent never spoke"
    assert tts.spoken[-1][1] == store.get_turn_version()

    # --- Invariant 3: final-state correctness ---
    with db_sf() as s:
        row = booking_to_dict(s.get(Booking, BOOKING_ID))
    assert row["date"] == FRIDAY, f"DB date {row['date']} != corrected {FRIDAY}"
    # never a merge: status came from the update path, date is the corrected one
    assert store.get_confirmed_state()["date"] == FRIDAY


def test_stale_write_never_persists(make_tm, db_sf):
    """Make the STALE call a write and prove it never reaches the DB."""
    tm, store, events, tts = make_tm("stale-write")

    async def scenario():
        # v1: slow *write* (cancel booking) in flight
        with db_sf() as s:
            original = booking_to_dict(s.get(Booking, BOOKING_ID))
        await tm.dispatch_tool("cancel_booking", {"booking_id": BOOKING_ID, "delay_ms": DELAY_MS})
        await asyncio.sleep(INTERRUPT_AT_MS / 1000.0)
        tm.on_interrupt()
        # v2: the real intent — a date change
        await tm.dispatch_tool("update_booking", {"booking_id": BOOKING_ID, "changes": {"date": FRIDAY}})
        await tm.join()
        return original

    original = asyncio.run(scenario())

    with db_sf() as s:
        row = booking_to_dict(s.get(Booking, BOOKING_ID))
    assert row["status"] != "cancelled", "stale cancel_booking corrupted the DB!"
    assert row["date"] == FRIDAY
    results = [e for e in events.to_dict()["events"] if e["type"] == "tool_call_result"]
    cancel_result = [e for e in results if e["tool"] == "cancel_booking"][0]
    assert cancel_result["action"] == "discarded_stale"


def test_noise_does_not_bump_version(make_tm, db_sf):
    """Silence / sub-threshold noise must not be treated as an interruption."""
    tm, store, events, tts = make_tm("noise")
    start = store.get_turn_version()
    # no on_interrupt() call -> version stays put
    tm.on_user_speech_start(transcript="")
    assert store.get_turn_version() == start
