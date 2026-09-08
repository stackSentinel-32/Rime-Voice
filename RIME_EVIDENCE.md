# Hard Voice Claim

> When the user interrupts during agent speech **or during a pending tool call**, queued
> Rime audio stops within **300 ms** of interruption detection, the stale tool result is
> **discarded — logged as `discarded_stale`, never spoken and never applied to state** (never
> written to the booking record), and the agent's next utterance and the final DB state reflect
> **only the user's latest request**.

This is a distributed-state / race-condition problem. The mechanism that proves it is a
**Redis-backed turn-versioning scheme** (see `agent/turn_manager.py` + `redis_state/session_store.py`).

## Acceptance Test

**Exact numbers (defined before building, per roadmap §1):**

| Parameter | Value | Env var |
|---|---|---|
| Injected tool-call delay | `4000 ms` | `TOOL_CALL_DELAY_MS` |
| Interrupt fired at | `1500 ms` into the call | `INTERRUPT_AT_MS` |
| TTS cancellation threshold | `< 300 ms` | `CANCEL_LATENCY_THRESHOLD_MS` |
| Warm-run repetitions | `5` | — |

**Setup**

- Fixture: `tests/fixtures/test_utterances.json` (mid-tool-call interruption case).
- A fixed `4000 ms` artificial delay is injected into the in-flight tool call (`TOOL_CALL_DELAY_MS`).
- Backends: hermetic run uses in-process `fakeredis` + SQLite; the full app uses real Redis +
  Postgres via `docker-compose.yml`. **The turn-versioning logic under test is identical in both.**

**Procedure** (see `tests/test_interruption.py`)

1. `t=0`   User asks to look up booking 12 for Thursday → dispatch slow `lookup_booking` (v1, 4000 ms delay). Agent starts speaking "Let me check…".
2. `t=1500ms` User interrupts, changing the request to Friday → `turn_version` bumps `v1→v2`, queued TTS is cancelled.
3. `t≈1500ms` Fresh `update_booking(date=Friday)` dispatched (v2).
4. `t≈4000ms` The original `lookup_booking` result finally arrives tagged v1; `v1 ≠ v2` → `discarded_stale`.
5. The v2 `update_booking` result (tagged v2, current v2) is applied and committed to the DB.

**Three invariants asserted** (roadmap §7):

1. **Cancellation latency** — `tts_cancel.t_ms − user_speech_start_interrupt.t_ms < 300 ms`.
2. **No stale application** — no `tool_call_result` with `turn_version_at_result != current_turn_version` ever has `action: applied`.
3. **Final-state correctness** — DB state after the run matches only the last (post-interruption) request, never a merge.

A second test (`test_stale_write_never_persists`) makes the stale call a **write** and asserts it
never reaches Postgres — the state-mutating commit is gated behind the non-stale check, so a
cancelled tool call cannot corrupt the booking record.

**Reproduce with:**

```bash
./evidence/run_acceptance_test.sh
# fast local check:
TOOL_CALL_DELAY_MS=800 INTERRUPT_AT_MS=300 ./evidence/run_acceptance_test.sh
```

## Result

Measured from a real run of `agent/turn_manager.py` (roadmap timings: 4000 ms injected tool
delay, interrupt at 1500 ms), N = 5 runs (1 cold + 4 warm). Regenerate with
`./evidence/run_acceptance_test.sh` or `python evidence/collect_evidence.py` — full log in
`evidence/event_log_sample.json`, numbers in `evidence/latency_summary.json`.

| Metric | Result |
|---|---|
| Orchestration cancel latency (cold) | **0.012 ms** |
| Orchestration cancel latency (warm, N=4) | **0.013–0.017 ms** |
| Cancellation threshold | 300 ms — **PASS (5/5 runs)** |
| Stale results discarded (`discarded_stale`) | **1/1** — never spoken, never committed |
| Fresh results applied | 1/1 |
| Final DB state correctness (date = corrected Friday, no merge) | **5/5 runs** |
| Version monotonicity (50 sequential + 30 concurrent increments, rapid double-interrupt) | **PASS** |

The orchestration cancel path is sub-millisecond because it is a version check + flag flip
in-process. **End-to-end audio cancellation** additionally includes the STT partial-transcript
floor (~150 ms) plus Rime/network transport — see Limitations.

The same scenario is replayable live from the dashboard (`/` → *Interruption Evidence* →
**Run interruption scenario**), which runs the identical code path (`agent/demo_scenario.py`)
against the deployment's real database — the dashboard and this file cannot drift.

## Limitations

- **STT partial-transcript latency floor (~150 ms).** In the live system, interruption cannot be
  detected until VAD/STT surfaces the barge-in. This ~150 ms is a floor on real-world reaction
  time and is *additional* to the orchestration cancel-path latency measured above.
- **What the automated test measures.** The hermetic test isolates the versioning/cancel logic
  (detection → version bump → TTS cancel → stale discard → commit gating) against in-process
  fakes. End-to-end audio cancellation additionally includes network + Rime streaming behavior.
- **Rime mid-stream cancellation (load-bearing, Phase 0).** The Rime client
  (`agent/tts_rime.py`) speaks the ws3 JSON WebSocket directly: cancel sends the
  documented `{"operation": "clear"}` (discards the synthesis buffer server-side) and
  drops locally buffered chunks, plus a browser-side queue clear. Unit-tested
  (`tests/test_worker_logic.py`); **the live ws3 `clear` path is exercised by the
  /ws/call smoke test and preflight, and must be re-verified with real keys at
  submission time — record the measured mechanism here.**
- **Fallback disclosure.** If STT falls back to Whisper-local, or the LLM/Rime uses any fallback
  path, it is surfaced at `/status` and in the UI header pills. The live call closes with a
  logged event (never a fake success) when keys are absent.
- **Synthetic data only.** All bookings are generated (`backend/seed.py`, deterministic seed 42).
  No real customer data anywhere.
