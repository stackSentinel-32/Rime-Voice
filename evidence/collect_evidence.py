#!/usr/bin/env python3
"""Generate evidence artifacts from a REAL run of the turn-versioning orchestrator.

Phase 6 done honestly: every event is logged during an actual run of
agent/turn_manager.py, using the SAME shared scenario as the /demo/run dashboard
endpoint (agent/demo_scenario.py) — one code path, so published evidence can never
drift from what the dashboard shows.

Writes:
  evidence/event_log_sample.json  full §5.4 event log from the cold run
  evidence/latency_summary.json   cold + warm cancellation latencies and outcomes

Usage:
  python evidence/collect_evidence.py
  TOOL_CALL_DELAY_MS=4000 INTERRUPT_AT_MS=1500 WARM_RUNS=4 python evidence/collect_evidence.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.demo_scenario import run_interruption_scenario  # noqa: E402
from backend.db import Base, make_engine, make_session_factory  # noqa: E402
from backend.seed import seed  # noqa: E402
from redis_state.session_store import make_redis_client  # noqa: E402

DELAY_MS = int(os.getenv("TOOL_CALL_DELAY_MS", "4000"))
INTERRUPT_AT_MS = int(os.getenv("INTERRUPT_AT_MS", "1500"))
THRESHOLD_MS = int(os.getenv("CANCEL_LATENCY_THRESHOLD_MS", "300"))
N_WARM = int(os.getenv("WARM_RUNS", "4"))
FRIDAY = "2026-09-18"
BOOKING_ID = 12


def main() -> int:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    seed(sf, n=40)
    redis = make_redis_client(use_fake=True)

    def run(sid):
        return asyncio.run(run_interruption_scenario(
            sf, redis, sid, DELAY_MS, INTERRUPT_AT_MS, threshold_ms=THRESHOLD_MS,
            booking_id=BOOKING_ID, corrected_date=FRIDAY,
        ))

    cold = run("evidence-cold")
    with open("evidence/event_log_sample.json", "w") as f:
        json.dump({"session_id": cold["session_id"], "events": cold["events"]}, f, indent=2)

    warm = [run(f"evidence-warm-{i}")["summary"]["cancel_latency_ms"] for i in range(N_WARM)]
    s = cold["summary"]
    summary = {
        "scenario": {"delay_ms": DELAY_MS, "interrupt_at_ms": INTERRUPT_AT_MS, "threshold_ms": THRESHOLD_MS},
        "cancellation_latency_ms": {"cold": s["cancel_latency_ms"], "warm": warm, "runs": 1 + len(warm)},
        "stale_discarded": s["stale_discarded"],
        "applied": s["applied"],
        "results_total": s["stale_discarded"] + s["applied"],
        "final_db_date": cold["after"]["date"],
        "final_db_status": cold["after"]["status"],
        "final_state_correct": s["final_state_correct"],
        "all_runs_pass": s["pass"],
    }
    os.makedirs("evidence", exist_ok=True)
    with open("evidence/latency_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(json.dumps(summary, indent=2))
    ok = (
        s["cancel_latency_ms"] < THRESHOLD_MS
        and all(l < THRESHOLD_MS for l in warm)
        and s["stale_discarded"] == 1
        and s["applied"] == 1
        and s["final_state_correct"]
    )
    print("\nEVIDENCE:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
