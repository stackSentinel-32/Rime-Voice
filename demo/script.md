# Demo Script — Glow & Go: Interruption-Hardened Voice Booking Agent
## DataForge · Pathway · Rime Hackathon — Submission Demo
### Target: 6:00 minutes | ~900 spoken words at ~150 wpm

> **One rule before you start:** Every number you say on camera must appear in
> `RIME_EVIDENCE.md` or on the dashboard you are looking at. No ad-libbing metrics.

---

## Pre-Demo Setup (T-30 min — do this before you go on stage)

| Step | Command | Must Show |
|---|---|---|
| Hermetic tests | `python -m pytest tests/ -v` | All pass (green) |
| Acceptance test | `./evidence/run_acceptance_test.sh` | `PASS` printed |
| Preflight check | `python evidence/preflight.py` | Rime synthesis OK, Groq OK |
| Launch app | `docker compose up --build` | All 3 containers healthy |
| Open dashboard | `http://localhost:8000` | Provider pills visible in header |
| Status check | `http://localhost:8000/status` | `rime_model: mistv3`, `llm_model: openai/gpt-oss-20b` |
| Booking 12 exists | Visible in DB seed | Resets automatically on every demo run |
| Backup ready | Dashboard tab open, phone hotspot on | In case of Wi-Fi failure |

---

## Scene 1 — The User, the Problem, and Why Voice Is Everything (0:00–1:00)

**[Screen: landing page `http://localhost:8000` — pitch diagram showing the race condition]**

> "Meet Riya — a receptionist at Glow & Go, a hair salon. She handles bookings, reschedules,
> and cancellations, all by voice.
>
> Here's what every other voice agent gets wrong. A customer calls in and says: 'Move my
> appointment to Thursday.' Riya starts looking it up, starts speaking — and the customer
> interrupts: 'Actually, make it Friday.' Riya says 'Got it.' But the booking still shows
> Thursday.
>
> Why? Because the database write was already in-flight when the interrupt happened. The old
> result landed, applied itself, and nobody noticed. That is not a UX problem. That is a
> distributed-state race condition — and it causes real booking corruption in production
> voice agents every day.
>
> Without voice, this problem doesn't exist. Without solving this problem, you can't ship a
> trustworthy voice booking product. That's why we chose it. Removing speech from Glow & Go
> doesn't leave you with a slower product — it leaves you with nothing.
>
> Let me show you exactly how we solved it."

**[Beat — 2 seconds. Let the problem sit with the judges.]**

---

## Scene 2 — The Mechanism: Turn-Versioning (1:00–2:15)

**[Screen: split view — `agent/turn_manager.py` on the left, `redis_state/session_store.py` on right]**

> "The solution is called turn-versioning. I'm going to explain it in one analogy, and then
> I'll show you the exact code.
>
> Think of a deli ticket machine. Every time you step up to order — every turn — you pull a
> numbered ticket. That number is your turn version. Right now: version 1.
>
> When you dispatch a tool call — say, 'look up booking 12' — we stamp that call with v1.
> The call goes out and takes time. Meanwhile, the agent starts speaking.
>
> Now you interrupt. Three things happen in under a millisecond:"

**[Highlight `on_interrupt` in turn_manager.py — lines 43-50]**

> "One: Redis INCR. The version atomically jumps to v2. One command. Uninterruptible even
> under rapid double-interrupts — we have a test proving 50 sequential and 30 concurrent
> increments all stay monotonic.
>
> Two: `tts.cancel()` — the Rime streaming WebSocket receives a JSON `{\"operation\": \"clear\"}`
> message, discarding its synthesis buffer server-side, and we stop forwarding any buffered
> audio chunks locally. The audio stops.
>
> Three: a fresh tool call goes out tagged v2 — the corrected Friday request.
>
> Four seconds later, the original Thursday lookup finally crawls back. It's tagged v1. Current
> version is v2. We check — one line of code."

**[Highlight `_on_tool_result` lines 67-76]**

> "`version_at_result != current` — it's stale. We log it as `discarded_stale`. It is never
> spoken. It never reaches the database. The commit gate is physically blocked. A stale call
> cannot reach `session.commit()` — that is a structural guarantee, not a convention.
>
> The v2 Friday update lands fresh. It commits. Riya confirms it. That's the entire mechanism."

---

## Scene 3 — Live Evidence: Watch It Happen (2:15–3:45)

**[Screen: `http://localhost:8000` → Evidence tab. Click "Run interruption scenario (exact roadmap timing)"]**

> "Now I'm running the actual acceptance test live — in-browser, against the real database,
> real Redis. Same code path as the published RIME_EVIDENCE.md artifacts. The dashboard
> cannot lie to you — it calls `agent/demo_scenario.py` directly.
>
> Parameters: 4 seconds of artificial tool delay, interrupt at 1.5 seconds. Watch."

**[Narrate as the timeline fires:]**

> "There — `tool_call_dispatch` tagged version 1. Agent speaks: 'Let me check booking 12 for
> Thursday...'
>
> At 1.5 seconds: `user_speech_start_interrupt`. Version jumps v1→v2. `tts_cancel` fires.
> Look at that cancel latency — **0.0105 milliseconds**. That is the measured orchestration
> path. Sub-millisecond.
>
> Four seconds in: the stale Thursday result arrives, tagged v1. Current is v2. The verdict:
> `discarded_stale`. Never spoken. Never written.
>
> v2 result applies. Commits to Postgres. Look at the database diff: Thursday on the left,
> Friday on the right. Clean. No merge. No phantom data. Correct — every single run."

**[Point at the three metric cards:]**

> "Three invariants. Cancel latency: **0.0105 ms** against a **300 ms** threshold — PASS.
> Stale writes: zero — PASS. Final state: correct — PASS. Five out of five runs.
>
> If a judge wants to run it themselves: `./evidence/run_acceptance_test.sh`. One command,
> no API keys, PASS in under 10 seconds."

---

## Scene 4 — Live Voice Call: Riya, Interrupted (3:45–5:00)

**[Screen: "Live Call" tab → click Connect. Mic orb pulses cyan "Listening"]**

> "Now the real thing. This is Riya, live.
>
> The architecture here is direct — no middleware, no LiveKit, no third-party transport.
> Your browser mic sends raw PCM audio over a single WebSocket to our server. Deepgram
> transcribes it in streaming partial results. Groq's `openai/gpt-oss-20b` — running at
> roughly 1000 tokens per second — decides what tool to call. The TurnManager dispatches it.
> And Rime's `mistv3` model, speaker `cove`, streams PCM audio back at 24 kilohertz. First
> audio arrives in about 37 milliseconds. Everything in one process."

**[Happy path first:]**

You: *"Hi Riya, can you check my booking number twelve?"*
[Riya speaks — Rime TTS audio plays back.]

> "Normal flow. Any agent can do this."

**[The kill shot — interrupt mid-sentence:]**

You: *"Now look up booking fifteen for me —"*
[Wait 1–2 seconds until Riya starts speaking, then cut in:]
You: *"—actually, forget that, move it to Friday."*

[Riya stops immediately. Confirms only the Friday update.]

> "She stopped mid-word. Not mid-sentence — mid-word. The old lookup was still in flight.
> It came back, found version 1 against current version 2, and was silently discarded.
> Booking 15 now shows Friday. Not a merge. Not a confused state. Exactly what the user
> actually said."

**[If live call fails — pivot cleanly:]**

> "We're having a network issue — that's a real constraint of live WebSocket demos and
> we're not going to pretend otherwise. But here's the thing: what you just saw on the
> dashboard is the identical code path. The mechanism is what matters, and it just ran
> live, correctly, in front of you. Our /status page shows which providers are configured —
> we never fake a green light."

---

## Scene 5 — Architecture & Stack (5:00–5:30)

**[Screen: VS Code file tree — open each file as you mention it]**

> "The whole repo is designed to be small enough that a judge can read it in 20 minutes.
>
> `agent/turn_manager.py` — 107 lines. The entire differentiator lives here.
>
> `redis_state/session_store.py` — 74 lines. One Redis INCR call is the entire atomicity
> guarantee.
>
> `backend/tools.py` — the validate/commit split. A stale call can compute a proposal but
> can never write.
>
> `backend/voice.py` — the live pipeline. Direct WebSocket, no LiveKit.
>
> `tests/` — 30 hermetic tests. Fakeredis. SQLite. Fake TTS. The race is reproducible
> without any API keys.
>
> Evidence lives in `evidence/` — four probe scripts that measured real failures we fixed:
> WebSocket compression was clumping audio into 1-second holes, Rime burst cadence needed
> precise buffer sizing. Every fix has a measurement."

---

## Scene 6 — What's Next: The Roadmap (5:30–5:55)

**[Screen: README — then a quick sketch or slide with bullet points]**

> "What we built today is the foundation. Here's where this goes next.
>
> **Telephone-grade reliability.** Port the same turn-versioning core onto a SIP/telephony
> bridge — this exact bug is catastrophically worse on phone, where you can't even see the
> UI. A patient calling a hospital who gets the wrong appointment committed because of a
> race condition is a serious safety issue. The mechanism we proved today is the fix.
>
> **Confidence-weighted barge-in detection.** Right now, our InterruptionDetector uses a
> simple noise gate. The next version uses STT confidence scores to distinguish a true
> barge-in from ambient noise — reducing false positives without adding latency.
>
> **Multi-turn state reconciliation.** Today, stale results are discarded cleanly. The next
> challenge: if a stale update was partially visible in the UI before being discarded, we
> need compensating rollback. That's an append-only event-log problem — and we already have
> the structured event log in `agent/event_log.py`.
>
> **Multilingual receptionist.** Rime supports multiple languages. Adding Hindi, Tamil, or
> regional language support to Riya — with the same interruption guarantees — would make
> this immediately useful for tier-2 Indian markets where voice is the primary interface."

---

## Scene 7 — Close (5:55–6:00)

> "Interruption handling is not a prompt-engineering problem. It is a distributed-state
> problem with race conditions, stale writes, and concurrent tool execution. We proved it
> with a mechanism, an acceptance test defined before building, and measured numbers.
>
> One command. No keys. Reproducible in 10 seconds. Thank you."

---

## Judging Criteria — How We Score

| Criterion | Weight | Our Answer |
|---|---|---|
| **Problem & voice necessity** | 25% | Booking corruption from stale writes = real harm. Remove voice, problem disappears. Remove the solution, product is broken. |
| **Hard voice engineering** | 25% | Mid-tool-call interruption with distributed-state consistency. Turn-versioning + Redis INCR + validate/commit split. Measured. |
| **Rime integration** | 20% | `mistv3` / `cove` / `en` / `wss://users-ws.rime.ai/ws3` / PCM 24kHz. Preflight verified. Server-side cancel via `{operation: clear}`. Central to every spoken output. |
| **Evidence & reproducibility** | 20% | `RIME_EVIDENCE.md`, `run_acceptance_test.sh`, 30 hermetic tests, 4 probe scripts, committed artifacts. One command, no keys. |
| **Demo clarity** | 10% | User → problem → normal flow → stress case → result → measured number → reproduce command. |

---

## Judge Q&A — 7 Answers You Need

| Question | Answer |
|---|---|
| **"Is 0.01 ms end-to-end?"** | No — explicitly disclosed. That's the orchestration path only: version check + Redis INCR + cancel flag. STT barge-in adds ~150 ms floor. Network and audio drain add more. We measured what we own. |
| **"Why Redis, not asyncio.Lock?"** | `INCR` is atomic across processes, replicas, and concurrent tasks. A Python lock would work in one process; Redis makes it correct when you scale horizontally or survive a restart. |
| **"What if Rime clear fails?"** | `tts_rime.py` sends `{operation: clear}` server-side AND drops local buffered chunks. Even if the network cancel is slow, the orchestration guarantee holds — worst case, a few extra audio frames before silence. The versioning is the real protection. |
| **"Can a stale write sneak through?"** | No. `backend/tools.py` splits writes into `validate()` (no persistence) and `commit_booking()`. TurnManager only calls `commit` after the non-stale check passes. The commit line is physically unreachable from a stale result path. |
| **"What if double-interrupt fires?"** | `test_version_monotonicity.py`: 50 sequential increments, 30 concurrent threads, rapid double-interrupt in the same millisecond — all monotonic. Redis INCR is atomic by definition. |
| **"Is the live voice real or mocked?"** | Real. Deepgram transcribes real mic audio, Groq drives real tool decisions, Rime synthesizes real speech. `/status` shows which providers are configured. Nothing is stubbed in the live-call path. |
| **"What does the evidence/ folder prove?"** | `probe_ws_delivery.py` measured that WebSocket per-message deflate compressed audio into >1s bursts — we disabled it. `probe_rime_cadence.py` measured Rime's burst pattern to size the audio queue. Every fix is a measured diagnosis, not a guess. |

---

## Full Repository Map

```
agent/
  turn_manager.py       107 lines. THE differentiator. Tags, compares, gates, discards.
  tts_rime.py           Rime ws3 streaming. {operation: clear} + buffer drop fallback.
  stt_pipeline.py       Deepgram streaming + InterruptionDetector with noise gate.
  llm_tools.py          Groq openai/gpt-oss-20b. Riya persona. 5 booking tools.
  demo_scenario.py      Single source of truth for the acceptance scenario.
  event_log.py          Structured JSON, monotonic milliseconds.
  tool_client.py        In-process (hermetic) or HTTP (live) — same interface.

backend/
  main.py               FastAPI. /ws/call, /demo/run, /status, /health.
  voice.py              VoiceCallSession. Full pipeline, no LiveKit.
  tools.py              validate() + commit_booking(). Stale writes structurally impossible.
  db.py + models.py     SQLAlchemy. Postgres (docker) or SQLite (hermetic).
  seed.py               40 bookings, seed 42, deterministic.

redis_state/
  session_store.py      INCR turn_version. Hash of pending tool tags. Confirmed state.

web/
  index.html            Single-file SPA. Evidence dashboard + live-call orb.
  audio-worklet.js      Browser AudioWorklet. PCM16 in, PCM16 out via WebSocket.

tests/
  test_interruption.py          3 invariants. Cancel latency, no stale write, final state.
  test_version_monotonicity.py  50 + 30 concurrent + rapid double-interrupt.
  test_worker_logic.py          TurnManager + InterruptionDetector integration.
  test_llm_tools.py             Groq tool-calling, offline mocked.
  test_receptionist_tools.py    CRUD logic.

evidence/
  run_acceptance_test.sh     One command. No keys. PASS.
  collect_evidence.py        Regenerates all artifacts.
  preflight.py               Live Rime synthesis + Groq model check + Deepgram WS auth.
  probe_ws_delivery.py       Measured: deflate causes >1s audio gaps. Fix: disable it.
  probe_rime_cadence.py      Measured: Rime burst size. Fix: correct queue cap.
  probe_pcm_capture.py       Measured: browser PCM capture quality.
  probe_straddle.py          Measured: chunk boundary correctness.
```

---

*Script version: 2026-09-10 | Stack: Rime mistv3/cove/en + Groq openai/gpt-oss-20b + Deepgram nova-2 + Redis + Postgres | Reproduce: `./evidence/run_acceptance_test.sh`*
