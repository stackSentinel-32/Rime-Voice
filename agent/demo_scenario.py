"""Shared mid-tool-call interruption scenario — single code path for evidence + demo.

Used by BOTH `evidence/collect_evidence.py` (acceptance artifacts) and the backend's
`/demo/run` endpoint (evidence dashboard). One implementation, so the dashboard can
never drift from the published evidence.

The target booking is reset to its seeded state before each run, so every run tells
the same deterministic story (Thursday -> corrected date) no matter how many times
the demo button is pressed.
"""
from __future__ import annotations

import asyncio

from agent.event_log import EventLogger
from agent.tool_client import LocalToolClient
from agent.turn_manager import TurnManager
from backend.models import Booking
from backend.seed import build_rows
from backend.tools import booking_to_dict
from redis_state.session_store import SessionStore

MODES = {
    "fast": {"delay_ms": 800, "interrupt_at_ms": 300},
    "exact": {"delay_ms": 4000, "interrupt_at_ms": 1500},
}


class _RecordingTTS:
    def __init__(self):
        self.spoken = []
        self.cancel_count = 0
        self.active = False

    def speak(self, text, v):
        self.active = True
        self.spoken.append((text, v))

    def cancel(self):
        self.active = False
        self.cancel_count += 1


def _reset_to_seed(session_factory, booking_id: int) -> dict:
    """Restore the booking to its deterministic seeded state (repeatable demos)."""
    seeded = {b.id: b for b in build_rows(n=40, seed=42)}
    with session_factory() as s:
        row = s.get(Booking, booking_id)
        if row is None:
            raise ValueError(f"booking {booking_id} not found")
        src = seeded.get(booking_id)
        if src is not None:
            for k in ("customer_name", "date", "time", "service_type", "status"):
                setattr(row, k, getattr(src, k))
            s.commit()
        return booking_to_dict(row)


async def run_interruption_scenario(
    session_factory,
    redis_client,
    session_id: str,
    delay_ms: int,
    interrupt_at_ms: int,
    threshold_ms: int = 300,
    booking_id: int = 12,
    corrected_date: str = "2026-09-18",
) -> dict:
    """Run roadmap §5.2: slow tool in flight -> user corrects mid-flight -> stale discard."""
    before = _reset_to_seed(session_factory, booking_id)

    store = SessionStore(redis_client, session_id)
    store.reset()
    events = EventLogger(session_id)
    tts = _RecordingTTS()
    tm = TurnManager(
        session_id, store, events, tts, LocalToolClient(session_factory),
        render=lambda n, p: f"Booking {p.get('id')} is now on {p.get('date')} ({p.get('status')}).",
    )

    tm.on_user_speech_start(f"Check my booking {booking_id} for Thursday")
    await tm.dispatch_tool("lookup_booking", {"booking_id": booking_id, "delay_ms": delay_ms})
    tts.speak(f"Let me check booking {booking_id} for Thursday...", store.get_turn_version())
    await asyncio.sleep(interrupt_at_ms / 1000)
    tm.on_interrupt(reason="interrupt")
    await tm.dispatch_tool("update_booking", {"booking_id": booking_id, "changes": {"date": corrected_date}})
    await tm.join()

    with session_factory() as s:
        after = booking_to_dict(s.get(Booking, booking_id))

    evs = events.to_dict()
    cancel = next(e for e in evs["events"] if e["type"] == "tts_cancel")
    results = [e for e in evs["events"] if e["type"] == "tool_call_result"]
    stale = [e for e in results if e["action"] == "discarded_stale"]
    applied = [e for e in results if e["action"] == "applied"]
    final_correct = after["date"] == corrected_date and after["status"] != "cancelled"
    summary = {
        "cancel_latency_ms": cancel["latency_ms"],
        "threshold_ms": threshold_ms,
        "stale_discarded": len(stale),
        "applied": len(applied),
        "final_state_correct": final_correct,
        "final_turn_version": store.get_turn_version(),
        "pass": bool(
            cancel["latency_ms"] < threshold_ms
            and len(stale) == 1
            and len(applied) == 1
            and final_correct
        ),
    }
    return {"session_id": session_id, "events": evs["events"],
            "before": before, "after": after, "summary": summary}
